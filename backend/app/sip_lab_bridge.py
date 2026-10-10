"""Opt-in internal ARI/UDP media echo lab. No customer data, ASR, LLM or TTS calls."""
import base64
import hashlib
import json
import math
import os
import pwd
import re
import signal
import socket
import sqlite3
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from struct import pack, unpack, unpack_from

import httpx

from .sip_lab import LabConfig, SIPLabError
from .sip_lab_media import MediaPacketError, ReceiveStream, decode_packet

APP = 'repayguard_media_lab'
ORIGIN = 'http://asterisk:8088/ari'
EVENTS = f'ws://asterisk:8088/ari/events?app={APP}'
ID = re.compile(r'[A-Za-z0-9_.:-]{1,80}\Z')


class BridgeError(RuntimeError):
    pass


def pcmu(pcm):
    if not isinstance(pcm, bytes) or len(pcm) % 2 or not 2 <= len(pcm) <= 1920:
        raise MediaPacketError('invalid_pcm_frame')
    output = bytearray()
    for sample in unpack(f'<{len(pcm) // 2}h', pcm):
        sign = 128 if sample < 0 else 0
        magnitude = min(abs(sample), 32635) + 132
        exponent = max(0, magnitude.bit_length() - 8)
        mantissa = (magnitude >> (exponent + 3)) & 15
        output.append((~(sign | (exponent << 4) | mantissa)) & 255)
    return bytes(output)


def owned_ids(instance, channel):
    if not ID.fullmatch(channel):
        raise BridgeError('invalid_channel_identifier')
    digest = hashlib.sha256(f'{instance}:{channel}'.encode()).hexdigest()[:32]
    return 'RG-MEDIA-' + digest, 'RG-BRIDGE-' + digest


class ResourceJournal:
    def __init__(self, path, instance):
        path = Path(path)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path)
        self.instance = instance
        self.db.execute('CREATE TABLE IF NOT EXISTS media_calls (instance TEXT, phone TEXT, day TEXT, state TEXT, '
                        'PRIMARY KEY(instance,phone))')
        self.db.commit()

    def reserve(self, phone):
        owned_ids(self.instance, phone)
        day = datetime.now(UTC).date().isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing = self.db.execute('SELECT state FROM media_calls WHERE instance=? AND phone=?',
                                       (self.instance, phone)).fetchone()
            if existing:
                raise BridgeError('existing_intent_cleanup_only')
            count = self.db.execute('SELECT count(*) FROM media_calls WHERE instance=? AND day=?',
                                    (self.instance, day)).fetchone()[0]
            if count >= 20:
                raise BridgeError('media_daily_reservation_limit')
            self.db.execute('INSERT INTO media_calls VALUES (?,?,?,?)', (self.instance, phone, day, 'pending'))
            self.db.commit()  # Before bridge/channel creation; no retries after restart.
        except BaseException:
            self.db.rollback()
            raise

    def mark(self, phone, state):
        self.db.execute('UPDATE media_calls SET state=? WHERE instance=? AND phone=?', (state, self.instance, phone))
        self.db.commit()

    def unfinished(self):
        return [row[0] for row in self.db.execute('SELECT phone FROM media_calls WHERE instance=? AND state!=?',
                                                (self.instance, 'closed'))]

    def close(self):
        self.db.close()


class MediaSession:
    def __init__(self, peer, now, transmit_ssrc):
        self.peer, self.started, self.next_send = peer, now, now
        self.stream = None
        self.queue = deque(maxlen=10)
        self.sequence = self.timestamp = 0
        self.transmit_ssrc = transmit_ssrc
        self.sent = self.invalid = self.foreign = self.queue_drops = 0
        self.tone_index = 0

    def receive(self, data, peer):
        if peer != self.peer:
            self.foreign += 1
            return
        try:
            if self.stream is None:
                source = unpack_from('!I', data, 8)[0] if len(data) >= 12 else -1
                decode_packet(data, payload_type=0, ssrc=source)
                stream = ReceiveStream(payload_type=0, ssrc=source)
            else:
                stream = self.stream
            frame = stream.consume(data)
            self.stream = stream
            self.process_audio(frame.pcm_s16le)
        except (MediaPacketError, ValueError):
            self.invalid += 1

    def process_audio(self, pcm):
        # Decode -> attenuate -> encode proves this is a program media path.
        samples = [sample // 2 for sample in unpack(f'<{len(pcm) // 2}h', pcm)]
        for offset in range(0, len(samples), 160):
            block = samples[offset:offset + 160]
            block += [0] * (160 - len(block))
            if len(self.queue) == self.queue.maxlen:
                self.queue_drops += 1
            self.queue.append(pcmu(pack('<160h', *block)))

    def close(self):
        self.queue.clear()

    def tick(self, udp, now):
        if now - self.started >= 60:
            return False
        if now < self.next_send:
            return True
        if self.tone_index < 50:
            frequency = 440 if self.tone_index < 25 else 660
            start = self.tone_index * 160
            samples = [int(2400 * math.sin(2 * math.pi * frequency * (start + i) / 8000)) for i in range(160)]
            payload = pcmu(pack('<160h', *samples))
            self.tone_index += 1
        else:
            payload = self.queue.popleft() if self.queue else b'\xff' * 160
        data = pack('!BBHII', 128, 0, self.sequence, self.timestamp, self.transmit_ssrc) + payload
        udp.sendto(data, self.peer)
        self.sequence = (self.sequence + 1) & 65535
        self.timestamp = (self.timestamp + 160) & 0xFFFFFFFF
        self.sent += 1
        self.next_send = now + 0.02  # No catch-up packet flood after scheduler stalls.
        return True

    def summary(self):
        received = self.stream.summary() if self.stream else {'packets_received': 0, 'samples_received': 0}
        return {'event': 'media_session_summary', 'received_packets': received['packets_received'],
                'decoded_samples': received['samples_received'], 'sent_packets': self.sent,
                'invalid_packets': self.invalid, 'foreign_packets': self.foreign, 'queue_drops': self.queue_drops,
                'audio_verified': False, 'ai_dialogue_ready': False, 'provider_calls': 0}


def request(client, method, path, *, params=None, identity=None):
    response = client.request(method, path, params=params)
    if response.status_code not in {200, 201, 204}:
        raise BridgeError('ari_request_failed')
    if identity is not None:
        try:
            if response.json().get('id') != identity:
                raise BridgeError('ari_identity_mismatch')
        except (ValueError, AttributeError) as exc:
            raise BridgeError('ari_invalid_response') from exc
    return response


def cleanup(client, journal, phone):
    media, bridge = owned_ids(journal.instance, phone)
    ok = True
    for path in (f'/channels/{media}', f'/channels/{phone}', f'/bridges/{bridge}'):
        try:
            response = client.delete(path)
            ok = response.status_code in {204, 404} and ok
        except httpx.HTTPError:
            ok = False
    journal.mark(phone, 'closed' if ok else 'unknown')
    return ok


def scoped_phone(event, extension='1002'):
    channel = event.get('channel')
    if not isinstance(channel, dict):
        return None
    dialplan = channel.get('dialplan', {})
    identifier = channel.get('id')
    if (extension in {'1002', '1003'} and event.get('type') == 'StasisStart' and event.get('application') == APP
            and isinstance(identifier, str) and ID.fullmatch(identifier)
            and isinstance(channel.get('name'), str) and channel['name'].startswith('PJSIP/1001-')
            and isinstance(dialplan, dict) and dialplan.get('context') == 'lab-echo'
            and dialplan.get('exten') == extension):
        return identifier
    return None


def prepare_session(client, journal, phone, gateway_ip, session_factory=MediaSession):
    journal.reserve(phone)
    media, bridge = owned_ids(journal.instance, phone)
    request(client, 'POST', f'/bridges/{bridge}', params={'type': 'mixing,proxy_media'}, identity=bridge)
    request(client, 'POST', '/channels/externalMedia', params={
        'channelId': media, 'app': APP, 'external_host': 'media:60000', 'format': 'ulaw',
        'encapsulation': 'rtp', 'transport': 'udp', 'connection_type': 'client', 'direction': 'both',
    }, identity=media)
    values = []
    for variable in ('UNICASTRTP_LOCAL_ADDRESS', 'UNICASTRTP_LOCAL_PORT'):
        try:
            value = request(client, 'GET', f'/channels/{media}/variable', params={'variable': variable}).json()['value']
            values.append(value)
        except (ValueError, KeyError, TypeError) as exc:
            raise BridgeError('invalid_rtp_destination') from exc
    try:
        port = int(values[1])
    except (ValueError, TypeError) as exc:
        raise BridgeError('invalid_rtp_destination') from exc
    if values[0] != gateway_ip or not 10000 <= port <= 10019:
        raise BridgeError('untrusted_rtp_destination')
    request(client, 'POST', f'/bridges/{bridge}/addChannel', params={'channel': f'{phone},{media}'})
    journal.mark(phone, 'active')
    return session_factory((gateway_ip, port), time.monotonic(), int.from_bytes(os.urandom(4)))


def run_loop(client, events, udp, journal, gateway_ip, stopping, *, extension='1002', session_factory=MediaSession,
             voice_provider='local'):
    active_phone = None
    session = None
    for phone in journal.unfinished():
        if not cleanup(client, journal, phone):
            raise BridgeError('recovery_cleanup_unconfirmed')
    if extension not in {'1002', '1003'}:
        raise BridgeError('invalid_lab_extension')
    voice = extension == '1003'
    print(json.dumps({'event': 'awaiting_linphone_' + extension, 'local_models_enabled': voice and voice_provider == 'local',
                      'cloud_models_enabled': voice and voice_provider == 'qwen_token_plan',
                      'model_calls_enabled': voice, 'business_ready': False}), flush=True)
    try:
        while not stopping():
            try:
                raw = events.recv(timeout=0.01)
                event = json.loads(raw)
                if not isinstance(event, dict):
                    continue
                incoming_voice = voice and scoped_phone(event, '1003') is not None
                phone = scoped_phone(event, extension) or (scoped_phone(event, '1002') if voice else None)
                if phone:
                    if active_phone == phone:
                        continue
                    if active_phone:
                        # Only this already-scoped incoming test caller; no originate.
                        client.delete(f'/channels/{phone}')
                    else:
                        active_phone = phone
                        try:
                            current_factory = session_factory if incoming_voice or not voice else MediaSession
                            session = prepare_session(client, journal, phone, gateway_ip, current_factory)
                            print(json.dumps({'event': 'program_media_connected', 'model_calls_enabled': incoming_voice,
                                              'business_ready': False}), flush=True)
                        except (BridgeError, httpx.HTTPError):
                            if not cleanup(client, journal, phone):
                                raise BridgeError('setup_cleanup_unconfirmed') from None
                            active_phone = None
                            print(json.dumps({'event': 'media_setup_failed'}), flush=True)
                elif event.get('type') in {'StasisEnd', 'ChannelDestroyed'} and active_phone:
                    channel = event.get('channel', {})
                    if isinstance(channel, dict) and channel.get('id') in {active_phone, owned_ids(journal.instance, active_phone)[0]}:
                        if session:
                            print(json.dumps(session.summary()), flush=True)
                            session.close()
                        if not cleanup(client, journal, active_phone):
                            raise BridgeError('end_cleanup_unconfirmed')
                        session = active_phone = None
            except TimeoutError:
                pass
            except (ValueError, TypeError):
                continue
            if session:
                for _ in range(50):
                    try:
                        data, peer = udp.recvfrom(4097)
                    except BlockingIOError:
                        break
                    session.receive(data, peer)
                if not session.tick(udp, time.monotonic()):
                    print(json.dumps(session.summary()), flush=True)
                    session.close()
                    if not cleanup(client, journal, active_phone):
                        raise BridgeError('timeout_cleanup_unconfirmed')
                    session = active_phone = None
            else:
                # Drain stale packets before the next single scoped call.
                for _ in range(50):
                    try:
                        udp.recvfrom(4097)
                    except BlockingIOError:
                        break
    finally:
        if active_phone:
            if session:
                print(json.dumps(session.summary()), flush=True)
                session.close()
            if not cleanup(client, journal, active_phone):
                print(json.dumps({'event': 'cleanup_unknown'}), flush=True)


def load_container_config():
    if os.getenv('SIP_LAB_MEDIA_ACKNOWLEDGED') != 'true':
        raise SIPLabError('own_test_phone_acknowledgement_required')
    # Parse only two allowlisted values, never execute/source a mounted shell file.
    values = {}
    for line in Path('/run/sip-lab/lab.env').read_text().splitlines():
        for name in ('SIP_LAB_INSTANCE_ID', 'SIP_LAB_ARI_PASSWORD'):
            prefix = 'export ' + name + '='
            if line.startswith(prefix):
                if name in values:
                    raise SIPLabError('ambiguous_lab_config')
                values[name] = line[len(prefix):]
    os.environ.update(values)
    config = LabConfig.from_environment()
    if os.geteuid() == 0:
        user = pwd.getpwnam('media_lab')
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    return config


def voice_factory(mode):
    if mode == 'echo':
        return MediaSession, '1002'
    if mode not in {'voice', 'qwen'}:
        raise BridgeError('invalid_media_mode')
    from .sip_lab_voice import QWEN_CONTAINER_URL, TOKEN, LocalVoiceClient
    from .sip_lab_voice_session import VoiceMediaSession
    cloud = mode == 'qwen'
    acknowledged = 'SIP_LAB_QWEN_VOICE_ACKNOWLEDGED' if cloud else 'SIP_LAB_LOCAL_VOICE_ACKNOWLEDGED'
    if os.getenv(acknowledged) != 'true':
        raise BridgeError('qwen_voice_acknowledgement_required' if cloud else 'local_voice_acknowledgement_required')
    name = 'qwen-voice.env' if cloud else 'local-voice.env'
    prefix = 'export SIP_LAB_QWEN_VOICE_TOKEN=' if cloud else 'export SIP_LAB_LOCAL_VOICE_TOKEN='
    # Only the private host connection token is mounted; no Provider API key is read here.
    lines = Path('/run/sip-lab/' + name).read_text().splitlines()
    values = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
    if len(values) != 1 or not TOKEN.fullmatch(values[0]):
        raise BridgeError('invalid_voice_connection_config')
    token = values[0]

    def factory(peer, now, source):
        client = LocalVoiceClient(token, url=QWEN_CONTAINER_URL, expected_provider='qwen_token_plan') if cloud else None
        return VoiceMediaSession(peer, now, source, token, client=client)

    return factory, '1003'


def main():
    from websockets.sync.client import connect

    journal = None
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        mode = os.getenv('SIP_LAB_MEDIA_MODE', 'echo')
        factory, extension = voice_factory(mode)
        config = load_container_config()
        gateway_ip = socket.gethostbyname('asterisk')
        journal = ResourceJournal('/var/lib/media-lab/calls.db', config.instance)
        authorization = base64.b64encode(f'repayguard_lab:{config.password}'.encode()).decode()
        with httpx.Client(base_url=ORIGIN, auth=('repayguard_lab', config.password), timeout=3,
                          trust_env=False, follow_redirects=False) as client, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(('0.0.0.0', 60000))
            udp.setblocking(False)
            # Startup wait only; once connected, a disconnect exits for journal recovery.
            deadline = time.monotonic() + 30
            while not stopped:
                try:
                    with connect(EVENTS, additional_headers={'Authorization': 'Basic ' + authorization},
                                 proxy=None, open_timeout=3, close_timeout=2, max_size=65536, max_queue=16) as events:
                        run_loop(client, events, udp, journal, gateway_ip, lambda: stopped,
                                 extension=extension, session_factory=factory,
                                 voice_provider='qwen_token_plan' if mode == 'qwen' else 'local')
                    return 0
                except (OSError, TimeoutError):
                    if journal.unfinished() or time.monotonic() >= deadline:
                        raise BridgeError('event_stream_unavailable') from None
                    time.sleep(0.5)
        return 0
    except Exception:
        # No exception/body/URL trace: they can contain credentials or channel details.
        print(json.dumps({'event': 'media_lab_unavailable', 'ai_dialogue_ready': False}), flush=True)
        return 2
    finally:
        if journal:
            journal.close()


if __name__ == '__main__':
    raise SystemExit(main())
