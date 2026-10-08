import importlib.util
import json
import socket
import threading
import time
from collections import deque
from pathlib import Path
from struct import pack, unpack

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.sip_lab_local_voice import create_app, execute_turn, load_manifest, main
from app.sip_lab_media import decode_packet
from app.sip_lab_voice import (
    HOST_URL,
    MODELS,
    PHRASE,
    LocalVoiceClient,
    Utterance,
    VoiceCancelled,
    VoiceError,
    validate_reply,
)
from app.sip_lab_voice_session import VoiceMediaSession

TOKEN = 'a' * 64
TURN = 'b' * 32


class Engine:
    warmed = True

    def __init__(self, text=PHRASE, reply='测试成功，请继续。', end=False):
        self.text, self.response, self.end = text, reply, end
        self.calls = []

    def asr(self, pcm, check):
        check()
        self.calls.append(('asr', len(pcm)))
        return self.text

    def reply(self, text, history, check):
        check()
        self.calls.append(('llm', list(history)))
        return self.response, self.end

    def tts(self, text, check):
        self.calls.append(('tts', text))
        for _ in range(2):
            check()
            yield pack('<1600h', *([2000] * 1600))


def command(kind='turn', turn=TURN, samples=1600):
    return {'kind': kind, 'turn': turn, 'samples': samples}


def drain(ws):
    audio = bytearray()
    while True:
        event = ws.receive_json()
        if event['kind'] == 'audio':
            value = ws.receive_bytes()
            assert len(value) == event['samples'] * 2
            audio.extend(value)
        else:
            return event, bytes(audio)


def test_ws_chain_auth_pairing_multiturn_context_and_no_raw_text():
    engine = Engine()
    with TestClient(create_app(engine, TOKEN)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}) as ws:
        ws.send_json(command())
        ws.send_bytes(b'\0' * 3200)
        event, audio = drain(ws)
        assert event['kind'] == 'complete' and len(audio) == 6400
        assert event['turn'] == TURN and not event['end']
        assert PHRASE not in json.dumps(event, ensure_ascii=False) and '_history' not in event
        ws.send_json(command(turn='c' * 32))
        ws.send_bytes(b'\0' * 3200)
        assert drain(ws)[0]['kind'] == 'complete'
        histories = [row[1] for row in engine.calls if row[0] == 'llm']
        assert histories[0] == [] and histories[1][0]['content'] == PHRASE


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer bad'},
                                    {'Authorization': 'Bearer ' + TOKEN, 'Origin': 'http://localhost'}])
def test_unauthorized_or_browser_sessions_never_call_engine(headers):
    engine = Engine()
    with TestClient(create_app(engine, TOKEN)) as client:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect('/lab/voice', headers=headers):
            pass
    assert not engine.calls


def test_global_single_session_gate():
    engine = Engine()
    with TestClient(create_app(engine, TOKEN)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
                '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}):
            pass
    assert not engine.calls


def test_disconnect_keeps_gate_until_uncancellable_inference_exits():
    started, release, exited = threading.Event(), threading.Event(), threading.Event()

    class Kernel(Engine):
        def asr(self, pcm, check):
            started.set()
            try:
                assert release.wait(2)
                check()
                return self.text
            finally:
                exited.set()

    engine = Kernel()
    headers = {'Authorization': 'Bearer ' + TOKEN}
    with TestClient(create_app(engine, TOKEN)) as client:
        try:
            with client.websocket_connect('/lab/voice', headers=headers) as ws:
                ws.send_json(command())
                ws.send_bytes(b'\0' * 3200)
                assert started.wait(1)
            with pytest.raises(WebSocketDisconnect), client.websocket_connect('/lab/voice', headers=headers):
                pass
        finally:
            release.set()
        assert exited.wait(1)
        until = time.monotonic() + 1
        while True:
            try:
                with client.websocket_connect('/lab/voice', headers=headers) as ws:
                    ws.send_json(command(kind='greeting', samples=0))
                    assert drain(ws)[0]['kind'] == 'complete'
                break
            except WebSocketDisconnect:
                assert time.monotonic() < until
                time.sleep(0.005)
    assert not any(row[0] == 'llm' for row in engine.calls)


@pytest.mark.parametrize('data,audio', [
    (command(samples=48001), None),
    (command(samples=True), None),
    (command(turn='../outside'), None),
    (command(), b'bad'),
    (command(kind='customer', samples=0), None),
    ({'kind': 'turn', 'turn': TURN, 'samples': 1600, 'url': 'http://outside'}, None),
])
def test_invalid_commands_and_audio_are_rejected_before_inference(data, audio):
    engine = Engine()
    with TestClient(create_app(engine, TOKEN)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}) as ws:
        ws.send_json(data)
        if audio is not None:
            ws.send_bytes(audio)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert not engine.calls


def test_deterministic_stop_and_financial_scope_precede_llm():
    for text, end in [('请结束测试', True), ('本金是多少', False)]:
        engine = Engine(text=text)
        result = execute_turn(engine, 'turn', b'\0' * 3200, deque(maxlen=8), threading.Event(), lambda _: None)
        assert result['end'] is end
        assert not any(row[0] == 'llm' for row in engine.calls)


def test_probe_matches_synthetic_phrase_and_cannot_accept_arbitrary_audio():
    engine = Engine(text='其他内容')
    with pytest.raises(VoiceError, match='asr_probe_phrase_mismatch'):
        execute_turn(engine, 'probe', None, [], threading.Event(), lambda _: None)
    assert not any(row[0] == 'llm' for row in engine.calls)


def test_cancel_during_llm_discards_audio_and_history_then_allows_next_turn():
    started = threading.Event()

    class Slow(Engine):
        def reply(self, text, history, check):
            started.set()
            until = time.monotonic() + 2
            while time.monotonic() < until:
                check()
                time.sleep(0.005)
            return super().reply(text, history, check)

    engine = Slow()
    with TestClient(create_app(engine, TOKEN)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}) as ws:
        ws.send_json(command())
        ws.send_bytes(b'\0' * 3200)
        assert started.wait(1)
        ws.send_json({'kind': 'cancel', 'turn': TURN})
        event, audio = drain(ws)
        assert event == {'kind': 'cancelled', 'turn': TURN} and audio == b''
        engine.reply = Engine.reply.__get__(engine)
        ws.send_json(command(turn='c' * 32))
        ws.send_bytes(b'\0' * 3200)
        assert drain(ws)[0]['kind'] == 'complete'
        assert [row[1] for row in engine.calls if row[0] == 'llm'] == [[]]


def test_cancel_before_inference_and_bounded_reply_validation():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(VoiceCancelled):
        execute_turn(Engine(), 'turn', b'\0' * 3200, [], cancel, lambda _: None)
    for value in [{'reply': '还款建议', 'end': False}, {'reply': 'x' * 61, 'end': False},
                  {'reply': 'test', 'end': 'false'}, {'reply': 'test', 'end': False, 'tool': 'write'},
                  {'reply': 'x\n', 'end': False}]:
        with pytest.raises(VoiceError):
            validate_reply(value)


def test_model_exception_diagnostics_never_emit_text_or_secrets():
    class Failed(Engine):
        def asr(self, pcm, check):
            raise RuntimeError('PRIVATE_PROVIDER_BODY_' + TOKEN)
    with TestClient(create_app(Failed(), TOKEN)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + TOKEN}) as ws:
        ws.send_json(command())
        ws.send_bytes(b'\0' * 3200)
        event, audio = drain(ws)
        assert event == {'kind': 'error', 'turn': TURN} and not audio


def test_vad_preroll_minimum_speech_endpoint_and_six_second_bound():
    voice = pack('<160h', *([1000] * 160))
    silence = b'\0' * 320
    vad = Utterance()
    for _ in range(4):
        assert vad.feed(silence) == []
    events = []
    for _ in range(15):
        events.extend(vad.feed(voice))
    for _ in range(30):
        events.extend(vad.feed(silence))
    assert events[0] == ('speech_start', None)
    assert events[1][0] == 'utterance' and 3200 <= len(events[1][1]) < 96000
    assert not vad.audio
    vad.reset()
    events = []
    for _ in range(400):
        events.extend(vad.feed(voice))
    assert len([e for e in events if e[0] == 'utterance']) == 1
    assert len([e[1] for e in events if e[0] == 'utterance'][0]) == 96000


def test_vad_short_noise_is_not_transcribed_and_partial_frames_are_reassembled():
    vad = Utterance()
    loud = pack('<160h', *([1000] * 160))
    assert vad.feed(loud[:160]) == []
    assert vad.feed(loud[160:]) == []
    assert vad.feed(loud) == []
    assert vad.feed(loud) == [('speech_start', None)]
    events = []
    for _ in range(30):
        events.extend(vad.feed(b'\0' * 320))
    assert events == []


def test_manifest_pins_paths_and_refuses_remote_code(tmp_path):
    data = {}
    for kind, model in MODELS.items():
        path = tmp_path / kind / ('c' * 40)
        path.mkdir(parents=True)
        (path / 'config.json').write_text('{}')
        data[kind] = {'model': model, 'revision': 'c' * 40, 'path': str(path)}
    (tmp_path / 'manifest.json').write_text(json.dumps(data))
    assert load_manifest(tmp_path) == data
    config = Path(data['asr']['path']) / 'config.json'
    config.write_text('{"tokenizer":{"auto_map":{"AutoTokenizer":"outside.Model"}}}')
    with pytest.raises(VoiceError, match='unsupported_remote_model_code'):
        load_manifest(tmp_path)
    config.write_text('{}')
    data['llm']['path'] = str(tmp_path / 'asr' / ('c' * 40))
    (tmp_path / 'manifest.json').write_text(json.dumps(data))
    with pytest.raises(VoiceError, match='invalid_local_model_path'):
        load_manifest(tmp_path)


def test_separate_credentials_are_private_exclusive_and_do_not_overwrite_sip(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts/sip-lab-local-voice-config.py'
    spec = importlib.util.spec_from_file_location('local_voice_config', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'lab.env').write_text('SYNTHETIC_ORIGINAL_CONFIG')
    module.generate(tmp_path)
    file = tmp_path / 'local-voice.env'
    assert file.stat().st_mode & 0o777 == 0o600
    original = file.read_text()
    with pytest.raises(FileExistsError):
        module.generate(tmp_path)
    assert file.read_text() == original
    assert (tmp_path / 'lab.env').read_text() == 'SYNTHETIC_ORIGINAL_CONFIG'


def test_actual_local_websocket_and_udp_to_voice_session_roundtrip():
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_app(Engine(), TOKEN), host='127.0.0.1', port=8090,
                                          log_level='critical', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    session = None
    try:
        until = time.monotonic() + 3
        while not server.started and thread.is_alive() and time.monotonic() < until:
            time.sleep(0.01)
        assert server.started
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as program, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as gateway:
            gateway.bind(('127.0.0.1', 0))
            gateway.settimeout(1)
            voice = LocalVoiceClient(TOKEN, url=HOST_URL)
            session = VoiceMediaSession(gateway.getsockname(), time.monotonic(), 42, TOKEN, client=voice)
            until = time.monotonic() + 3
            audio = None
            while time.monotonic() < until:
                audio = voice.pop()
                if audio:
                    break
                assert not voice.failed
                time.sleep(0.01)
            assert audio is not None
            with voice.lock:
                voice.audio.appendleft(audio)
            assert session.tick(program, time.monotonic())
            data, _ = gateway.recvfrom(4096)
            frame = decode_packet(data, payload_type=0, ssrc=42)
            assert abs(unpack('<160h', frame.pcm_s16le)[0] - 2000) < 100
            # Barge-in discards buffered generated speech immediately.
            for _ in range(3):
                session.process_audio(pack('<160h', *([1500] * 160)))
            assert voice.summary()['interruptions'] == 1
            assert voice.pop() is None
            session.close()
            session.close()
            assert not session.tick(program, time.monotonic())
    finally:
        if session:
            session.close()
        server.should_exit = True
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_cli_prepare_requires_ack_and_serve_is_optin(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['local-voice', 'prepare'])
    assert main() == 2
    assert 'local_voice_unavailable' in capsys.readouterr().out


def test_native_llm_api_cancellation_eos_budget_and_no_thinking(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from app.sip_lab_voice_adapters import QwenLLMAdapter
    lm, samplers = ModuleType('mlx_lm'), ModuleType('mlx_lm.sample_utils')
    seen = []
    results = [SimpleNamespace(text='{"reply":"测试完成。","end":false}', finish_reason='stop')]
    def generate(model, tokenizer, prompt, **kwargs):
        seen.append(kwargs)
        yield from results
    lm.stream_generate = generate
    samplers.make_sampler = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, 'mlx_lm', lm)
    monkeypatch.setitem(sys.modules, 'mlx_lm.sample_utils', samplers)
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            assert kwargs['enable_thinking'] is False and kwargs['tokenize'] is False
            assert messages[-1]['content'] == PHRASE
            return 'synthetic-prompt'
    engine = QwenLLMAdapter(object(), Tokenizer())
    assert engine.reply(PHRASE, [], lambda: None) == ('测试完成。', False)
    assert seen[0]['max_tokens'] == 128 and seen[0]['sampler'] == {'temp': 0}
    results[0].finish_reason = 'length'
    with pytest.raises(VoiceError, match='llm_incomplete'):
        engine.reply(PHRASE, [], lambda: None)


def test_client_stale_header_and_binary_are_discarded_after_interrupt():
    from contextlib import contextmanager

    from app.sip_lab_voice import digest
    client = None
    class Socket:
        def __init__(self):
            self.turn = None
            self.step = 0
        def send(self, raw):
            value = json.loads(raw)
            if value['kind'] == 'greeting':
                self.turn = value['turn']
        def recv(self, timeout):
            self.step += 1
            if self.step == 1:
                return json.dumps({'kind': 'audio', 'turn': self.turn, 'samples': 160})
            if self.step == 2:
                client.interrupt()
                return pack('<160h', *([2000] * 160))
            if self.step == 3:
                return json.dumps({'kind': 'complete', 'turn': self.turn, 'end': True,
                                   'reply_digest': digest('测试'), 'elapsed_ms': 10, 'first_audio_ms': 2})
            client.stop.set()
            raise TimeoutError
    @contextmanager
    def connect(*args, **kwargs):
        assert kwargs['proxy'] is None and kwargs['additional_headers']['Authorization'] == 'Bearer ' + TOKEN
        yield Socket()
    client = LocalVoiceClient(TOKEN, connect=connect)
    client.submit(None, kind='greeting')
    client._run()
    assert client.pop() is None and client.completed == 0 and not client.ended and not client.failed


def test_client_submission_and_playback_are_bounded():
    client = LocalVoiceClient(TOKEN)
    for _ in range(8):
        client.submit(b'\0' * 3200)
    with pytest.raises(VoiceError, match='voice_turn_budget_exhausted'):
        client.submit(b'\0' * 3200)
    assert client.submitted == 8
    client.close()
    assert client.pending is None and client.pop() is None
