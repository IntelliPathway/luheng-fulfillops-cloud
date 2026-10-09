"""Explicit paid synthetic cloud probe, independent from phone/business execution."""
import argparse
import getpass
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager

import httpx

from .sip_lab import LabConfig

WS = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
CHAT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
ASR = 'fun-asr-flash-8k-realtime-2026-01-28'
LLM = 'qwen-plus-2025-12-01'
TTS = 'cosyvoice-v3-flash'
PHRASE = '今天是语音链路测试'


class ProbeError(RuntimeError):
    pass


@contextmanager
def websocket(key, timeout):
    from websockets.sync.client import connect
    with connect(WS, additional_headers={'Authorization': 'Bearer ' + key}, proxy=None,
                 open_timeout=min(timeout, 5), close_timeout=2, max_size=65536, max_queue=16) as connection:
        yield connection


class VoiceProbe:
    def __init__(self, key, *, ws_factory=websocket, client=None, clock=time.monotonic, sleep=time.sleep):
        # This probe uses fixed pay-as-you-go endpoints, not subscription endpoints.
        # Both Token Plan and Coding Plan use sk-sp- keys; never send one here.
        if isinstance(key, str) and key.startswith('sk-sp-'):
            raise ProbeError('subscription_key_not_supported')
        self.key, self.ws_factory, self.clock, self.sleep = key, ws_factory, clock, sleep
        self.deadline = clock() + 60
        self.client = client
        self.metrics = []
        self.active_stage = None

    def remaining(self):
        value = self.deadline - self.clock()
        if value <= 0:
            raise ProbeError('probe_deadline_exceeded')
        return value

    @staticmethod
    def command(action, task, payload):
        return json.dumps({'header': {'action': action, 'task_id': task, 'streaming': 'duplex'},
                           'payload': payload}, ensure_ascii=False)

    def event(self, ws, task):
        raw = ws.recv(timeout=min(15, self.remaining()))
        if isinstance(raw, bytes):
            return raw
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ProbeError('invalid_speech_event')
        try:
            data = json.loads(raw)
            header = data['header']
            if header['task_id'] != task or header.get('event') == 'task-failed':
                raise ProbeError('speech_task_failed_or_mismatched')
            if header.get('event') not in {'task-started', 'result-generated', 'task-finished'}:
                raise ProbeError('unexpected_speech_event')
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise ProbeError('invalid_speech_event') from exc

    def start(self, ws, task, payload):
        ws.send(self.command('run-task', task, payload))
        event = self.event(ws, task)
        if not isinstance(event, dict) or event['header']['event'] != 'task-started':
            raise ProbeError('speech_task_not_started')

    def tts(self, text):
        self.active_stage = 'tts'
        if not isinstance(text, str) or not 1 <= len(text) <= 60:
            raise ProbeError('invalid_synthetic_text')
        task = str(uuid.uuid4())
        start = self.clock()
        first = None
        audio = bytearray()
        with self.ws_factory(self.key, self.remaining()) as ws:
            self.start(ws, task, {'task_group': 'audio', 'task': 'tts', 'function': 'SpeechSynthesizer',
                                 'model': TTS, 'parameters': {'text_type': 'PlainText', 'voice': 'longanyang',
                                 'format': 'pcm', 'sample_rate': 8000}, 'input': {}})
            ws.send(self.command('continue-task', task, {'input': {'text': text}}))
            ws.send(self.command('finish-task', task, {'input': {}}))
            finished = False
            for _ in range(256):
                event = self.event(ws, task)
                if isinstance(event, bytes):
                    if first is None:
                        first = self.clock()
                    if len(audio) + len(event) > 160000:
                        raise ProbeError('tts_audio_budget_exceeded')
                    audio.extend(event)
                elif event['header']['event'] == 'task-finished':
                    finished = True
                    break
            if not finished or not audio or len(audio) % 2:
                raise ProbeError('tts_incomplete_audio')
        self.metrics.append({'stage': 'tts', 'first_audio_ms': round((first - start) * 1000),
                             'elapsed_ms': round((self.clock() - start) * 1000)})
        return bytes(audio)

    def asr(self, pcm):
        self.active_stage = 'asr'
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 160000:
            raise ProbeError('invalid_synthetic_pcm')
        task = str(uuid.uuid4())
        start = self.clock()
        sentences = {}
        with self.ws_factory(self.key, self.remaining()) as ws:
            self.start(ws, task, {'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                                 'model': ASR, 'parameters': {'format': 'pcm', 'sample_rate': 8000}, 'input': {}})
            for offset in range(0, len(pcm), 1600):
                self.remaining()
                ws.send(pcm[offset:offset + 1600])
                self.sleep(0.1)
            ws.send(self.command('finish-task', task, {'input': {}}))
            finished = False
            for _ in range(256):
                event = self.event(ws, task)
                if isinstance(event, bytes):
                    raise ProbeError('unexpected_asr_binary')
                if event['header']['event'] == 'task-finished':
                    finished = True
                    break
                if event['header']['event'] == 'result-generated':
                    sentence = event.get('payload', {}).get('output', {}).get('sentence', {})
                    if sentence.get('heartbeat') or sentence.get('sentence_end') is not True:
                        continue
                    text = sentence.get('text')
                    number = sentence.get('sentence_id')
                    if type(number) is not int or not 1 <= number <= 16 or not isinstance(text, str) or len(text) > 120:
                        raise ProbeError('invalid_asr_sentence')
                    sentences[number] = text
            if not finished or not sentences:
                raise ProbeError('asr_incomplete')
        text = ''.join(sentences[number] for number in sorted(sentences))
        # Only known synthetic source may reach this model probe; no arbitrary speech.
        if re.sub(r'[\W_]', '', text) != PHRASE:
            raise ProbeError('asr_synthetic_phrase_mismatch')
        self.metrics.append({'stage': 'asr', 'elapsed_ms': round((self.clock() - start) * 1000)})
        return text

    def llm(self, text):
        self.active_stage = 'llm'
        if not isinstance(text, str) or re.sub(r'[\W_]', '', text) != PHRASE:
            raise ProbeError('llm_synthetic_phrase_mismatch')
        start = self.clock()
        payload = {'model': LLM, 'enable_thinking': False, 'max_tokens': 128,
                   'response_format': {'type': 'json_object'}, 'messages': [
                       {'role': 'system', 'content': '你是内部语音链路测试助手。只输出 JSON，字段 reply 为不超过60字的测试确认，end 为 true。禁止输出账务、合同或催收建议。'},
                       {'role': 'user', 'content': text}]}
        client = self.client or httpx.Client(trust_env=False, follow_redirects=False, timeout=8)
        try:
            with client.stream('POST', CHAT, headers={'Authorization': 'Bearer ' + self.key}, json=payload,
                               timeout=min(8, self.remaining())) as response:
                if response.status_code != 200:
                    raise ProbeError('llm_request_failed')
                body = bytearray()
                for chunk in response.iter_bytes():
                    self.remaining()
                    if len(body) + len(chunk) > 65536:
                        raise ProbeError('llm_response_budget_exceeded')
                    body.extend(chunk)
            data = json.loads(body)
            choice = data['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ProbeError('llm_incomplete')
            content = json.loads(choice['message']['content'])
            reply = content['reply']
            if set(content) != {'reply', 'end'} or content['end'] is not True or not isinstance(reply, str) or not 1 <= len(reply) <= 60:
                raise ProbeError('llm_invalid_test_reply')
            if any(word in reply for word in ('欠款', '本金', '余额', '还款', '合同', '催收')):
                raise ProbeError('llm_off_scope_reply')
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProbeError('llm_invalid_response') from exc
        finally:
            if self.client is None:
                client.close()
        self.metrics.append({'stage': 'llm', 'elapsed_ms': round((self.clock() - start) * 1000)})
        return reply

    def run(self):
        source = self.tts(PHRASE)
        text = self.asr(source)
        reply = self.llm(text)
        output = self.tts(reply)
        return {'mode': 'synthetic_cloud_probe', 'service_chain_completed': True,
                'asr_phrase_matched': True, 'reply_digest': hashlib.sha256(reply.encode()).hexdigest(),
                'tts_samples': len(output) // 2, 'models': {'asr': ASR, 'llm': LLM, 'tts': TTS},
                'stages': self.metrics, 'phone_audio_verified': False, 'business_ready': False}


def main():
    parser = argparse.ArgumentParser(description='北京百炼按量付费合成语音链路测试：最多4次服务调用，不支持套餐 Key。')
    parser.add_argument('--acknowledged', action='store_true')
    args = parser.parse_args()
    probe = None
    try:
        LabConfig.from_environment()
        if not args.acknowledged or os.getenv('ENABLE_SIP_LAB_VOICE_TEST') != 'true':
            raise ProbeError('synthetic_paid_probe_not_enabled')
        key = os.getenv('DASHSCOPE_API_KEY') or getpass.getpass('百炼北京按量付费 API Key（不支持套餐 Key；不回显，不保存）：')
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', key):
            raise ProbeError('invalid_api_key_format')
        probe = VoiceProbe(key)
        print(json.dumps(probe.run(), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'mode': 'synthetic_cloud_probe', 'service_chain_completed': False,
                          'completed_stages': [stage['stage'] for stage in probe.metrics] if probe else [],
                          'failed_stage': probe.active_stage if probe else None,
                          'error': str(exc) if isinstance(exc, ProbeError) else 'voice_probe_unavailable_or_failed',
                          'phone_audio_verified': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
