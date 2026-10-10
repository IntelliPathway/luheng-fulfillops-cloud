"""Bounded local test voice protocol and VAD. No customer or business integration."""
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import deque
from struct import unpack

# Backward-compatible baseline identifiers; actual service identity comes from completion metadata.
MODELS = {
    'asr': 'mlx-community/Qwen3-ASR-1.7B-8bit',
    'llm': 'mlx-community/Qwen3-30B-A3B-Instruct-2507-8bit',
    'tts': 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit',
}
CONTAINER_URL = 'ws://host.docker.internal:8090/lab/voice'
HOST_URL = 'ws://127.0.0.1:8090/lab/voice'
QWEN_HOST_URL = 'ws://127.0.0.1:8092/lab/voice'
QWEN_CONTAINER_URL = 'ws://host.docker.internal:8092/lab/voice'
PHRASE = '今天是语音链路测试'
GREETING = '这里是本地语音测试，请说一句测试短句。说结束测试即可结束。'
TOKEN = re.compile(r'[a-f0-9]{64}\Z')
TURN = re.compile(r'[a-f0-9]{32}\Z')


class VoiceError(RuntimeError):
    pass


class VoiceCancelled(VoiceError):
    pass


def connect_without_redirects(url, **kwargs):
    try:
        from websockets.sync.client import reconnect
    except ImportError:
        raise VoiceError('websocket_dependency_incompatible') from None

    class FixedDestination(reconnect):
        def process_redirect(self, exc):
            return exc

    # Context-manager entry connects once. Never iterate the reconnect object.
    return FixedDestination(url, **kwargs)


def local_token():
    value = os.getenv('SIP_LAB_LOCAL_VOICE_TOKEN', '')
    if not TOKEN.fullmatch(value):
        raise VoiceError('invalid_local_voice_token')
    return value


def validate_reply(value):
    if not isinstance(value, dict) or set(value) != {'reply', 'end'}:
        raise VoiceError('invalid_reply_shape')
    reply = value['reply']
    if not isinstance(reply, str) or not 1 <= len(reply) <= 60 or type(value['end']) is not bool:
        raise VoiceError('invalid_reply_value')
    if any(word in reply for word in ('欠款', '本金', '余额', '还款', '合同', '催收', '<think', 'http')):
        raise VoiceError('off_scope_reply')
    if any(ord(character) < 32 for character in reply):
        raise VoiceError('invalid_reply_control')
    return reply, value['end']


class Utterance:
    """Energy VAD on accepted 8 kHz PCM: 100 ms preroll, 600 ms endpoint, 6 s cap."""
    def __init__(self):
        self.remainder = bytearray()
        self.preroll = deque(maxlen=5)
        self.audio = bytearray()
        self.voiced = self.silence = 0
        self.started = False

    def reset(self):
        self.remainder.clear()
        self.preroll.clear()
        self.audio.clear()
        self.voiced = self.silence = 0
        self.started = False

    def feed(self, pcm):
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 1920:
            raise VoiceError('invalid_vad_frame')
        self.remainder.extend(pcm)
        events = []
        while len(self.remainder) >= 320:
            frame = bytes(self.remainder[:320])
            del self.remainder[:320]
            samples = unpack('<160h', frame)
            loud = math.sqrt(sum(sample * sample for sample in samples) / 160) >= 350
            if not self.audio:
                self.preroll.append(frame)
                if loud:
                    self.voiced += 1
                else:
                    self.voiced = 0
                if self.voiced >= 3:
                    self.audio.extend(b''.join(self.preroll))
                    self.preroll.clear()
                    self.started = True
                    self.silence = 0
                    events.append(('speech_start', None))
            else:
                self.audio.extend(frame)
                self.voiced += int(loud)
                self.silence = 0 if loud else self.silence + 1
                if self.silence >= 30 or len(self.audio) >= 96000:
                    if self.voiced >= 10:
                        # Keep at most 100 ms trailing silence for ASR.
                        trim = max(0, self.silence - 5) * 320
                        events.append(('utterance', bytes(self.audio[:len(self.audio) - trim])[:96000]))
                    self.audio.clear()
                    self.voiced = self.silence = 0
                    self.started = False
        return events


class LocalVoiceClient:
    """One WS, one remote job, latest turn only. Reader never blocks the RTP scheduler."""
    def __init__(self, token, *, url=CONTAINER_URL, connect=None, clock=time.monotonic,
                 expected_config_digest=None, expected_revisions=None, expected_provider=None):
        cloud = expected_provider == 'qwen_token_plan'
        destinations = {QWEN_HOST_URL, QWEN_CONTAINER_URL} if cloud else {CONTAINER_URL, HOST_URL}
        if (not TOKEN.fullmatch(token) or url not in destinations or expected_provider not in {None, 'qwen_token_plan'}
                or (cloud and (expected_config_digest is not None or expected_revisions is not None))):
            raise VoiceError('invalid_local_voice_destination')
        self.token, self.url, self.connect, self.clock = token, url, connect, clock
        self.expected_provider = expected_provider
        self.provider = None
        self.cloud_provider_calls = 0
        self.provider_request_state = 'not_sent'
        self.provider_error = self.provider_active_stage = None
        self.connection_stage = 'not_started'
        if expected_config_digest is not None and not TOKEN.fullmatch(expected_config_digest):
            raise VoiceError('invalid_expected_configuration')
        self.expected_config_digest = expected_config_digest
        if expected_revisions is not None and (not isinstance(expected_revisions, dict) or set(
                expected_revisions) != {'asr', 'llm', 'tts'} or any(not isinstance(value, str) or not re.fullmatch(
                    r'[a-f0-9]{40}', value) for value in expected_revisions.values())):
            raise VoiceError('invalid_expected_revisions')
        self.expected_revisions = dict(expected_revisions) if expected_revisions else None
        self.configuration = self.error_code = None
        self.lock = threading.Lock()
        self.audio = deque()
        self.pending = None
        self.epoch = 0
        self.stop = threading.Event()
        self.thread = None
        self.failed = self.ended = False
        self.completed = self.interruptions = self.submitted = 0
        self.active = False
        self.metrics = deque(maxlen=8)

    def start(self, kind='greeting'):
        self.submit(None, kind=kind)
        self.thread = threading.Thread(target=self._run, daemon=True, name='local-voice-reader')
        self.thread.start()

    def submit(self, pcm, *, kind='turn'):
        if kind not in {'turn', 'greeting', 'probe'} or (kind == 'turn' and (
                not isinstance(pcm, bytes) or len(pcm) % 2 or not 3200 <= len(pcm) <= 96000)):
            raise VoiceError('invalid_voice_submission')
        with self.lock:
            if self.submitted >= 8 or self.stop.is_set() or self.failed:
                raise VoiceError('voice_turn_budget_exhausted')
            self.epoch += 1
            self.audio.clear()
            self.ended = False
            self.pending = (self.epoch, uuid.uuid4().hex, kind, pcm)
            self.submitted += 1
            self.active = True

    def interrupt(self):
        with self.lock:
            if self.active or self.audio:
                self.epoch += 1  # Any in-flight result is now stale, including pending binary.
                self.pending = None
                self.audio.clear()
                self.ended = False
                self.interruptions += 1
                self.active = False

    def pop(self):
        with self.lock:
            return self.audio.popleft() if self.audio else None

    def finished(self):
        with self.lock:
            return self.failed or (self.ended and not self.audio and not self.active)

    def close(self):
        self.stop.set()
        with self.lock:
            self.epoch += 1
            self.pending = None
            self.audio.clear()
        if self.thread:
            self.thread.join(timeout=0.5)

    def summary(self):
        with self.lock:
            result = {'local_turns_submitted': self.submitted, 'local_turns_completed': self.completed,
                    'interruptions': self.interruptions, 'local_voice_failed': self.failed,
                    'turn_metrics': list(self.metrics), 'configuration': self.configuration,
                    'local_voice_error': self.error_code}
            if self.expected_provider:
                result.update(provider=self.provider, cloud_provider_calls=self.cloud_provider_calls,
                              provider_request_state=self.provider_request_state, provider_error=self.provider_error,
                              provider_active_stage=self.provider_active_stage, provider_call_count_scope='last_host_event',
                              host_connection_stage=self.connection_stage)
            return result

    def provider_event(self, event):
        from .sip_lab_qwen_voice import MAX_CALLS, SAFE_ERRORS, validate_provider
        provider = validate_provider(event.get('provider'))
        count = event.get('cloud_provider_calls')
        state, error, stage = (event.get(key) for key in (
            'provider_request_state', 'provider_error', 'provider_active_stage'))
        if (self.provider != provider or type(count) is not int or not self.cloud_provider_calls <= count <= MAX_CALLS
                or state not in {'not_sent', 'unknown', 'completed', 'rejected'} or error not in SAFE_ERRORS | {None}
                or stage not in {None, 'asr', 'llm', 'tts'}):
            raise VoiceError('invalid_qwen_provider_event')
        self.cloud_provider_calls, self.provider_request_state = count, state
        self.provider_error, self.provider_active_stage = error, stage

    def _run(self):
        default_connect = connect_without_redirects
        deadline = self.clock() + 65
        remote = None
        binary = None
        cancelled = False
        try:
            self.connection_stage = 'host_connect'
            with (self.connect or default_connect)(self.url, additional_headers={
                    'Authorization': 'Bearer ' + self.token}, proxy=None, open_timeout=3,
                    close_timeout=1, max_size=65536, max_queue=8) as ws:
                if self.expected_provider:
                    self.connection_stage = 'provider_handshake'
                    from .sip_lab_qwen_voice import validate_provider
                    raw = ws.recv(timeout=3)
                    if not isinstance(raw, str) or len(raw) > 4096:
                        raise VoiceError('invalid_qwen_provider_handshake')
                    event = json.loads(raw)
                    if not isinstance(event, dict) or set(event) != {'kind', 'provider'} or event['kind'] != 'ready':
                        raise VoiceError('invalid_qwen_provider_handshake')
                    with self.lock:
                        self.provider = validate_provider(event['provider'])
                self.connection_stage = 'voice_turn'
                while not self.stop.is_set() and self.clock() < deadline:
                    commands = []
                    with self.lock:
                        if remote and remote[0] != self.epoch and not cancelled:
                            commands.append(json.dumps({'kind': 'cancel', 'turn': remote[1]}))
                            cancelled = True
                        if remote is None and self.pending:
                            remote, self.pending = self.pending, None
                            cancelled = False
                            _, turn, kind, pcm = remote
                            commands.append(json.dumps({'kind': kind, 'turn': turn,
                                                        'samples': len(pcm) // 2 if pcm else 0}))
                            if pcm:
                                commands.append(pcm)
                    # Network writes must never hold the RTP-facing playback lock.
                    for command in commands:
                        ws.send(command)
                    try:
                        raw = ws.recv(timeout=0.02)
                    except TimeoutError:
                        continue
                    if isinstance(raw, bytes):
                        if binary is None or len(raw) != binary['samples'] * 2:
                            raise VoiceError('unexpected_voice_binary')
                        with self.lock:
                            if remote and binary['turn'] == remote[1] and remote[0] == self.epoch:
                                for offset in range(0, len(raw), 320):
                                    if len(self.audio) >= 500:
                                        raise VoiceError('voice_playback_budget_exceeded')
                                    block = raw[offset:offset + 320]
                                    self.audio.append(block + b'\0' * (320 - len(block)))
                        binary = None
                        continue
                    if binary is not None or not isinstance(raw, str) or len(raw) > 4096:
                        raise VoiceError('invalid_voice_event')
                    event = json.loads(raw)
                    if not isinstance(event, dict) or not remote or event.get('turn') != remote[1]:
                        raise VoiceError('voice_turn_mismatch')
                    kind = event.get('kind')
                    if kind == 'audio':
                        if type(event.get('samples')) is not int or not 1 <= event['samples'] <= 1600:
                            raise VoiceError('invalid_voice_audio_count')
                        binary = event
                    elif kind in {'complete', 'cancelled', 'error'}:
                        with self.lock:
                            if self.expected_provider:
                                self.provider_event(event)
                            if kind == 'complete':
                                configuration = event.get('configuration')
                                if configuration is not None:
                                    from .sip_lab_voice_config import validate_service_configuration
                                    validate_service_configuration(configuration)
                                    if self.configuration and self.configuration != configuration:
                                        raise VoiceError('local_voice_configuration_changed')
                                    self.configuration = configuration
                                if self.expected_config_digest is not None and (
                                        configuration is None or configuration['config_digest'] != self.expected_config_digest):
                                    raise VoiceError('local_voice_configuration_mismatch')
                                if self.expected_revisions is not None and (
                                        configuration is None or configuration['revisions'] != self.expected_revisions):
                                    raise VoiceError('local_voice_revision_mismatch')
                            if kind == 'error':
                                self.failed = True
                                self.audio.clear()
                                if self.expected_provider:
                                    self.error_code = self.provider_error or 'qwen_host_inference_failed'
                            elif kind == 'complete' and remote[0] == self.epoch:
                                if type(event.get('end')) is not bool or not re.fullmatch(
                                        r'[a-f0-9]{64}', event.get('reply_digest', '')):
                                    raise VoiceError('invalid_voice_completion')
                                self.completed += 1
                                self.ended = event['end']
                                metrics = {'elapsed_ms': bounded_ms(event.get('elapsed_ms')),
                                           'first_audio_ms': bounded_ms(event.get('first_audio_ms'))}
                                stages = event.get('stage_ms')
                                if stages is not None:
                                    if not isinstance(stages, dict) or set(stages) != {
                                            'source_tts', 'asr', 'llm', 'reply_tts'}:
                                        raise VoiceError('invalid_voice_timing')
                                    metrics['stage_ms'] = {key: bounded_ms(value) for key, value in stages.items()}
                                self.metrics.append(metrics)
                            # Generation completion is not playback completion.
                            self.active = self.pending is not None
                        remote = None
                    else:
                        raise VoiceError('unexpected_voice_event')
                    if self.failed:
                        break
        except Exception as exc:
            with self.lock:
                self.failed = True
                self.audio.clear()
                self.error_code = str(exc) if isinstance(exc, VoiceError) and str(exc) in {
                    'local_voice_configuration_changed', 'local_voice_configuration_mismatch',
                    'local_voice_revision_mismatch', 'invalid_service_configuration',
                    'qwen_voice_provider_mismatch', 'invalid_qwen_provider_handshake',
                    'invalid_qwen_provider_event', 'websocket_dependency_incompatible'} else 'local_voice_service_failed'
                if self.expected_provider and self.error_code == 'local_voice_service_failed':
                    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
                    if isinstance(exc, ConnectionRefusedError):
                        self.error_code = 'qwen_host_connection_refused'
                    elif isinstance(exc, TimeoutError):
                        self.error_code = 'qwen_host_timeout'
                    elif isinstance(exc, InvalidStatus):
                        self.error_code = ('qwen_host_auth_or_session_denied' if exc.response.status_code == 403
                                           else 'qwen_host_handshake_rejected')
                    elif isinstance(exc, ConnectionClosed):
                        self.error_code = 'qwen_host_connection_closed'
                    elif isinstance(exc, InvalidHandshake):
                        self.error_code = 'qwen_host_handshake_rejected'
                    elif isinstance(exc, OSError):
                        self.error_code = 'qwen_host_network_failed'


def bounded_ms(value):
    if type(value) is not int or not 0 <= value <= 65000:
        raise VoiceError('invalid_voice_timing')
    return value


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()
