import importlib.util
import io
import json
import socket
import threading
import time
import wave
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from struct import pack, unpack

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import sip_lab_qwen_voice as module
from app.sip_lab_local_voice import create_app
from app.sip_lab_media import decode_packet
from app.sip_lab_qwen_probe import CASES, CHAT, KEY_ENV, MODELS
from app.sip_lab_qwen_voice import TokenVoiceEngine, execute_qwen_turn, provider_configuration
from app.sip_lab_voice import QWEN_HOST_URL, LocalVoiceClient, VoiceCancelled, VoiceError
from app.sip_lab_voice_session import VoiceMediaSession

KEY = "sk-sp-SYNTHETIC_PHONE_TEST_KEY_123456789"
TOKEN = "a" * 64
TURN = "b" * 32
PCM = pack("<1600h", *([2000] * 1600))


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv(module.TOKEN_ENV, raising=False)


class SpeechSocket:
    def __init__(self, calls, *, chunks=None, failure=None):
        self.calls = calls
        self.events = deque()
        self.task = None
        self.chunks = chunks if chunks is not None else [PCM, PCM]
        self.failure = failure

    def send(self, raw):
        data = json.loads(raw)
        self.calls.append(data)
        if self.task is None:
            self.task = data["header"]["task_id"]
            self.events.append(self.event("task-started"))
        if data["header"]["action"] == "finish-task":
            self.events.extend(
                [self.event(self.failure)] if self.failure else [*self.chunks, self.event("task-finished")]
            )

    def event(self, name):
        return json.dumps({"header": {"task_id": self.task, "event": name, "error_message": KEY}})

    def recv(self, timeout):
        assert timeout == 0.05
        return self.events.popleft()


def make_engine(text=CASES["confirm"][0], *, asr_response=None, llm_response=None, chunks=None, failure=None):
    commands, requests, closed = [], [], []

    @contextmanager
    def ws(key, timeout):
        assert key == KEY and timeout == 3
        try:
            yield SpeechSocket(commands, chunks=chunks, failure=failure)
        finally:
            closed.append(True)

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + KEY
        if str(request.url) == module.ASR_URL:
            return asr_response if asr_response is not None else httpx.Response(200, json={"output": {"text": text}})
        assert str(request.url) == CHAT
        case = json.loads(request.content)["messages"][-1]["content"]
        reply = "测试数字1234，测试日期10月9日。" if case == CASES["numbers_date"][0] else "语音测试确认。"
        return (
            llm_response
            if llm_response is not None
            else httpx.Response(
                200,
                json={
                    "model": MODELS[0],
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps({"reply": reply, "end": False})}}
                    ],
                    "usage": {"prompt_tokens": 105, "completion_tokens": 19},
                },
            )
        )

    engine = TokenVoiceEngine(KEY, transport=httpx.MockTransport(handle), ws_factory=ws)
    return engine, commands, requests, closed


def no_cancel():
    pass


def command(turn=TURN, kind="turn", samples=1600):
    return {"kind": kind, "turn": turn, "samples": samples}


def drain(ws):
    audio = bytearray()
    while True:
        event = ws.receive_json()
        if event["kind"] != "audio":
            return event, bytes(audio)
        value = ws.receive_bytes()
        assert len(value) == event["samples"] * 2
        audio.extend(value)


def test_real_service_protocol_multiturn_synthetic_history_and_redacted_diagnostics():
    engine, commands, requests, _ = make_engine()
    with (
        TestClient(create_app(engine, TOKEN, turn_executor=execute_qwen_turn)) as client,
        client.websocket_connect("/lab/voice", headers={"Authorization": "Bearer " + TOKEN}) as ws,
    ):
        assert ws.receive_json() == {"kind": "ready", "provider": provider_configuration()}
        assert not requests and not commands  # Startup and handshake never infer.
        for turn in (TURN, "c" * 32):
            ws.send_json(command(turn))
            ws.send_bytes(PCM)
            event, audio = drain(ws)
            assert event["kind"] == "complete" and event["turn"] == turn and audio == PCM * 2
            assert event["provider"] == provider_configuration() and not event["end"]
            assert KEY not in json.dumps(event) and CASES["confirm"][0] not in json.dumps(event, ensure_ascii=False)
            assert "_history" not in event
        assert event["cloud_provider_calls"] == 6
    llm = [json.loads(request.content) for request in requests if str(request.url) == CHAT]
    assert len(llm[0]["messages"]) == 2 and len(llm[1]["messages"]) == 4
    assert llm[1]["messages"][1]["content"] == CASES["confirm"][0]
    asr = json.loads(requests[0].content)
    uri = asr["input"]["messages"][0]["content"][0]["input_audio"]["data"]
    with wave.open(io.BytesIO(module.base64.b64decode(uri.split(",", 1)[1])), "rb") as wav:
        assert wav.getframerate() == 8000 and wav.readframes(wav.getnframes()) == PCM


@pytest.mark.parametrize(
    "text,case,end,calls",
    [
        ("我不是要结束测试。", "negation", False, 3),
        ("不要结束测试", "negation", False, 3),
        ("请结束测试", "stop", True, 2),
        ("客户合同信息", None, False, 2),
        ("请复述数字一二三四和日期十月九日", "numbers_date", False, 3),
        ("PRIVATE_CUSTOMER_FACT", None, False, 2),
    ],
)
def test_fixed_case_routing_end_negation_and_no_arbitrary_text_to_llm(text, case, end, calls):
    engine, _, requests, _ = make_engine(text)
    emitted = []
    result = execute_qwen_turn(engine, "turn", PCM, [], threading.Event(), emitted.append)
    assert module.test_case(text) == case and result["end"] is end and engine.external_calls == calls
    llm = [json.loads(request.content) for request in requests if str(request.url) == CHAT]
    assert len(llm) == (1 if case in {"negation", "numbers_date"} else 0)
    if llm:
        assert llm[0]["messages"][-1]["content"] == CASES[case][0]


def test_streaming_yields_before_provider_finishes_and_closes_on_generator_cancel():
    engine, commands, requests, closed = make_engine()
    stream = engine.tts("语音测试。", no_cancel)
    assert next(stream) == PCM and not closed and not requests
    assert engine.request_state == "unknown" and engine.external_calls == 1
    stream.close()
    assert closed == [True] and len(commands) == 3


def test_pcm_stream_reassembles_odd_packets_without_losing_samples():
    engine, _, _, _ = make_engine(chunks=[PCM[:1], PCM[1:]])
    assert b"".join(engine.tts("语音测试。", no_cancel)) == PCM


@pytest.mark.parametrize(
    "chunks,error",
    [([b"x"], "tts_incomplete_audio"), ([b"x" * 160002], "tts_audio_budget_exceeded"), ([], "tts_incomplete_audio")],
)
def test_tts_audio_integrity_failures_stop_without_retry(chunks, error):
    engine, _, requests, closed = make_engine(chunks=chunks)
    with pytest.raises(VoiceError, match=error):
        b"".join(engine.tts("语音测试。", no_cancel))
    assert engine.external_calls == 1 and len(closed) == 1 and not requests


def test_cancellation_during_asr_prevents_llm_and_tts_calls():
    cancel = threading.Event()
    calls = []

    def handle(request):
        calls.append(request)
        cancel.set()
        return httpx.Response(200, json={"output": {"text": CASES["confirm"][0]}})

    engine = TokenVoiceEngine(KEY, transport=httpx.MockTransport(handle))
    with pytest.raises(VoiceCancelled):
        execute_qwen_turn(engine, "turn", PCM, [], cancel, lambda _: pytest.fail("must not emit"))
    assert len(calls) == engine.external_calls == 1


def test_cancel_tts_discards_old_history_and_new_turn_starts_after_old_worker_exits():
    engine, _, requests, _ = make_engine()
    first_chunk, release = threading.Event(), threading.Event()
    original = engine.tts

    def slow_tts(text, check):
        for block in original(text, check):
            yield block
            first_chunk.set()
            assert release.wait(2)
            check()

    engine.tts = slow_tts
    with (
        TestClient(create_app(engine, TOKEN, turn_executor=execute_qwen_turn)) as client,
        client.websocket_connect("/lab/voice", headers={"Authorization": "Bearer " + TOKEN}) as ws,
    ):
        ws.receive_json()
        ws.send_json(command())
        ws.send_bytes(PCM)
        assert first_chunk.wait(1)
        assert ws.receive_json()["kind"] == "audio"
        ws.receive_bytes()
        ws.send_json({"kind": "cancel", "turn": TURN})
        release.set()
        event, _ = drain(ws)
        assert event["kind"] == "cancelled" and event["cloud_provider_calls"] == 3
        engine.tts = original
        ws.send_json(command("c" * 32))
        ws.send_bytes(PCM)
        event, audio = drain(ws)
        assert event["kind"] == "complete" and audio == PCM * 2
    payloads = [json.loads(request.content) for request in requests if str(request.url) == CHAT]
    assert len(payloads) == 2 and all(len(payload["messages"]) == 2 for payload in payloads)


@pytest.mark.parametrize(
    "code,error",
    [
        (401, "authentication_failed"),
        (403, "model_or_plan_denied"),
        (429, "quota_or_rate_limited"),
        (302, "redirect_denied"),
    ],
)
def test_cloud_rejections_stop_chain_and_propagate_only_safe_diagnostics(code, error):
    engine, _, requests, _ = make_engine(
        asr_response=httpx.Response(code, headers={"Location": CHAT}, json={"error": KEY})
    )
    with (
        TestClient(create_app(engine, TOKEN, turn_executor=execute_qwen_turn)) as client,
        client.websocket_connect("/lab/voice", headers={"Authorization": "Bearer " + TOKEN}) as ws,
    ):
        ws.receive_json()
        ws.send_json(command())
        ws.send_bytes(PCM)
        event, audio = drain(ws)
    assert event["kind"] == "error" and not audio and len(requests) == 1
    assert event["provider_error"] == error and event["provider_request_state"] == "rejected"
    assert event["cloud_provider_calls"] == 1 and KEY not in json.dumps(event)


def test_history_injection_rejected_before_provider_and_session_budget_is_bounded():
    engine, _, requests, _ = make_engine()
    with pytest.raises(VoiceError, match="invalid_test_history"):
        engine.reply(
            "confirm", [{"role": "user", "content": KEY}, {"role": "assistant", "content": "测试。"}], no_cancel
        )
    assert not requests
    engine.external_calls = module.MAX_CALLS
    with pytest.raises(VoiceError, match="qwen_session_call_budget_exceeded"):
        engine.asr(PCM, no_cancel)
    assert not requests
    engine.begin_session()
    assert engine.external_calls == 0


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer " + TOKEN, "Origin": "https://example.com"}],
)
def test_phone_host_auth_and_browser_rejection_before_any_cloud_call(headers):
    engine, commands, requests, _ = make_engine()
    with TestClient(create_app(engine, TOKEN, turn_executor=execute_qwen_turn)) as client:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/lab/voice", headers=headers):
            pass
    assert not commands and not requests and engine.external_calls == 0


def test_wrong_host_handshake_sends_no_test_audio_or_model_command():
    sent = []

    class Wrong:
        def recv(self, timeout):
            provider = provider_configuration()
            provider["models"]["llm"] = "other-model"
            return json.dumps({"kind": "ready", "provider": provider})

        def send(self, raw):
            sent.append(raw)

    @contextmanager
    def connect(*args, **kwargs):
        yield Wrong()

    voice = LocalVoiceClient(TOKEN, url=QWEN_HOST_URL, expected_provider="qwen_token_plan", connect=connect)
    voice.start()
    voice.thread.join(timeout=1)
    assert voice.failed and voice.error_code == "qwen_voice_provider_mismatch" and not sent
    voice.close()


def test_separate_token_generation_reuses_valid_file_and_rejects_symlink(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/sip-lab-qwen-voice-config.py"
    spec = importlib.util.spec_from_file_location("qwen_voice_config", script)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    (tmp_path / "lab.env").write_text("SYNTHETIC_EXISTING_LAB")
    assert config.generate(tmp_path) is True
    file = tmp_path / "qwen-voice.env"
    original = file.read_text()
    assert file.stat().st_mode & 0o777 == 0o600 and KEY not in original
    assert config.generate(tmp_path) is False and file.read_text() == original
    file.chmod(0o644)
    with pytest.raises(ValueError):
        config.generate(tmp_path)
    file.unlink()
    file.symlink_to(tmp_path / "lab.env")
    with pytest.raises(ValueError):
        config.generate(tmp_path)
    assert (tmp_path / "lab.env").read_text() == "SYNTHETIC_EXISTING_LAB"


def test_media_qwen_mode_uses_fixed_host_and_private_token_not_cloud_key(monkeypatch):
    from app import sip_lab_bridge

    monkeypatch.setenv("SIP_LAB_QWEN_VOICE_ACKNOWLEDGED", "true")
    monkeypatch.setattr(sip_lab_bridge.Path, "read_text", lambda _: "export SIP_LAB_QWEN_VOICE_TOKEN=" + TOKEN + "\n")
    factory, extension = sip_lab_bridge.voice_factory("qwen")
    monkeypatch.setattr(LocalVoiceClient, "start", lambda _: None)
    session = factory(("127.0.0.1", 1234), time.monotonic(), 42)
    assert extension == "1003" and session.voice.url == "ws://host.docker.internal:8092/lab/voice"
    assert session.voice.expected_provider == "qwen_token_plan"
    assert session.summary()["mode"] == "qwen_token_plan_voice_lab" and session.summary()["cloud_provider_calls"] == 0
    session.close()
    monkeypatch.delenv("SIP_LAB_QWEN_VOICE_ACKNOWLEDGED")
    with pytest.raises(sip_lab_bridge.BridgeError, match="qwen_voice_acknowledgement_required"):
        sip_lab_bridge.voice_factory("qwen")


def test_real_loopback_host_to_udp_pcmu_with_synthetic_cloud_protocol():
    import uvicorn

    engine, _, _, _ = make_engine()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(engine, TOKEN, turn_executor=execute_qwen_turn),
            host="127.0.0.1",
            port=8092,
            loop="asyncio",
            http="h11",
            log_level="critical",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    session = None

    def wait_idle():
        # Read-only check; never retry inference while releasing the old session.
        until = time.monotonic() + 2
        with httpx.Client(trust_env=False) as client:
            while client.get("http://127.0.0.1:8092/lab/status", headers={"Authorization": "Bearer " + TOKEN}).json()[
                "busy"
            ]:
                assert time.monotonic() < until
                time.sleep(0.01)

    try:
        until = time.monotonic() + 3
        while not server.started and thread.is_alive() and time.monotonic() < until:
            time.sleep(0.01)
        assert server.started
        doctor = module.doctor_host(TOKEN)
        assert doctor["host_ready"] and doctor["external_calls"] == 0 and engine.external_calls == 0
        assert module.doctor_host("c" * 64)["error"] == "qwen_host_auth_failed"
        with pytest.raises(module.HostProbeError, match="qwen_host_auth_or_session_denied") as denied:
            module.probe_host("c" * 64)
        assert denied.value.diagnostics["cloud_provider_calls"] == 0
        assert denied.value.diagnostics["host_connection_stage"] == "host_connect"
        probe = module.probe_host(TOKEN)
        assert probe["service_chain_completed"] and probe["asr_phrase_matched"] and probe["cloud_provider_calls"] == 4
        wait_idle()
        original_transport = engine.transport
        engine.transport = httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": KEY}))
        with pytest.raises(module.HostProbeError, match="authentication_failed") as failure:
            module.probe_host(TOKEN)
        report = failure.value.diagnostics
        assert report["cloud_provider_calls"] == 2 and report["provider_active_stage"] == "asr"
        assert report["provider_request_state"] == "rejected" and KEY not in json.dumps(report)
        wait_idle()
        engine.transport = original_transport
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as program,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as gateway,
        ):
            gateway.bind(("127.0.0.1", 0))
            gateway.settimeout(1)
            voice = LocalVoiceClient(TOKEN, url=QWEN_HOST_URL, expected_provider="qwen_token_plan")
            session = VoiceMediaSession(gateway.getsockname(), time.monotonic(), 42, TOKEN, client=voice)
            until = time.monotonic() + 3
            while not voice.completed and not voice.failed and time.monotonic() < until:
                time.sleep(0.01)
            assert voice.completed == 1 and not voice.failed
            assert session.tick(program, time.monotonic())
            data, _ = gateway.recvfrom(4096)
            frame = decode_packet(data, payload_type=0, ssrc=42)
            assert abs(unpack("<160h", frame.pcm_s16le)[0] - 2000) < 100
            summary = session.summary()
            assert summary["cloud_provider_calls"] == 1 and summary["provider"] == provider_configuration()
            for _ in range(3):
                session.process_audio(pack("<160h", *([1500] * 160)))
            assert voice.summary()["interruptions"] == 1 and voice.pop() is None
    finally:
        if session:
            session.close()
        server.should_exit = True
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_cli_status_zero_calls_and_serve_preflight_no_credential_leak(monkeypatch, capsys):
    monkeypatch.setattr(module.sys, "argv", ["host", "status"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("must not prompt"))
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["external_calls"] == 0 and result["host"] == QWEN_HOST_URL
    monkeypatch.setattr(module.sys, "argv", ["host", "serve"])
    assert module.main() == 2
    assert json.loads(capsys.readouterr().out)["error"] == "qwen_voice_acknowledgement_required"


def test_probe_failure_stage_and_provider_errors_do_not_leak_private_bodies():
    engine, _, _, _ = make_engine(text="未知识别结果")
    with pytest.raises(VoiceError, match="asr_probe_phrase_mismatch"):
        execute_qwen_turn(engine, "probe", None, [], threading.Event(), lambda _: None)
    assert engine.external_calls == 2
    engine.record_error(RuntimeError(KEY))
    assert engine.diagnostics()["provider_error"] == "qwen_host_inference_failed"
    assert KEY not in json.dumps(engine.diagnostics())


def test_actual_websocket_redirect_is_not_followed_or_retried():
    from websockets.sync.server import serve

    from app.sip_lab_voice import connect_without_redirects

    requests = []

    def redirect(connection, request):
        requests.append(request)
        response = connection.respond(302, "synthetic redirect")
        response.headers["Location"] = "ws://127.0.0.1:1/should-not-connect"
        return response

    with serve(lambda _: None, "127.0.0.1", 0, process_request=redirect) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with (
                pytest.raises(Exception) as error,
                connect_without_redirects(
                    f"ws://127.0.0.1:{port}",
                    additional_headers={"Authorization": "Bearer " + KEY},
                    proxy=None,
                    open_timeout=1,
                    close_timeout=1,
                ),
            ):
                pytest.fail("redirect must fail")
            from websockets.exceptions import InvalidStatus

            assert isinstance(error.value, InvalidStatus) and error.value.response.status_code == 302
        finally:
            server.shutdown()
            thread.join(timeout=2)
    assert len(requests) == 1


def test_old_websocket_api_is_reported_before_connection_or_cloud_call(monkeypatch, capsys):
    import websockets.sync.client

    from app.sip_lab import LabConfig
    from app.sip_lab_voice import connect_without_redirects

    monkeypatch.delattr(websockets.sync.client, "reconnect")
    monkeypatch.setattr(module.importlib.metadata, "version", lambda _: "15.0.1")
    report = module.websocket_dependencies()
    assert report["websockets_version"] == "15.0.1" and not report["websocket_no_redirect_api_available"]
    with pytest.raises(VoiceError, match="websocket_dependency_incompatible"):
        connect_without_redirects(QWEN_HOST_URL)
    monkeypatch.setattr(LabConfig, "from_environment", lambda: None)
    monkeypatch.setenv(module.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(module.sys, "argv", ["host", "serve", "--acknowledged"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("must fail before key prompt"))
    assert module.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "dependency_check" and report["error"] == "websocket_dependency_incompatible"


@pytest.mark.parametrize(
    "failure,expected",
    [(ConnectionRefusedError(KEY), "qwen_host_connection_refused"), (TimeoutError(KEY), "qwen_host_timeout")],
)
def test_connection_failure_diagnostics_do_not_leak_exception_text(failure, expected):
    @contextmanager
    def connect(*args, **kwargs):
        raise failure
        yield  # Make the failure occur at context entry.

    voice = LocalVoiceClient(TOKEN, url=QWEN_HOST_URL, expected_provider="qwen_token_plan", connect=connect)
    voice.start(kind="probe")
    voice.thread.join(timeout=1)
    assert voice.failed and voice.error_code == expected
    assert voice.summary()["host_connection_stage"] == "host_connect"
    assert KEY not in json.dumps(voice.summary())
    voice.close()


@pytest.mark.parametrize(
    "status,data,expected",
    [
        (403, {"detail": KEY}, "qwen_host_auth_failed"),
        (302, {"detail": KEY}, "qwen_host_status_rejected"),
        (
            200,
            {"provider": provider_configuration(), "busy": True, "draining": False, "warmed": True},
            "qwen_host_busy",
        ),
        (
            200,
            {"provider": provider_configuration(), "busy": False, "draining": True, "warmed": True},
            "qwen_host_not_ready",
        ),
        (200, {"provider": {"kind": KEY}}, "qwen_voice_provider_mismatch"),
        (200, {"provider": provider_configuration(), "busy": "false"}, "invalid_qwen_host_status"),
        (200, {"detail": KEY * 200}, "invalid_qwen_host_status"),
    ],
)
def test_doctor_is_bounded_read_only_and_redacts_host_errors(status, data, expected):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET" and str(request.url) == "http://127.0.0.1:8092/lab/status"
        return httpx.Response(status, json=data, headers={"Location": "https://example.com/should-not-follow"})

    report = module.doctor_host(TOKEN, transport=httpx.MockTransport(handle))
    assert report["error"] == expected and report["host_reachable"] and report["external_calls"] == 0
    assert len(requests) == 1 and KEY not in json.dumps(report)


def test_provider_failure_reaches_probe_cli_with_stage_and_call_count(monkeypatch, capsys):
    from app.sip_lab import LabConfig

    sent = []

    class FailedHost:
        def recv(self, timeout):
            if not sent:
                return json.dumps({"kind": "ready", "provider": provider_configuration()})
            command = json.loads(sent[0])
            return json.dumps(
                {
                    "kind": "error",
                    "turn": command["turn"],
                    "provider": provider_configuration(),
                    "cloud_provider_calls": 2,
                    "provider_request_state": "rejected",
                    "provider_error": "authentication_failed",
                    "provider_active_stage": "asr",
                }
            )

        def send(self, raw):
            sent.append(raw)

    @contextmanager
    def connect(*args, **kwargs):
        yield FailedHost()

    monkeypatch.setattr(
        module, "LocalVoiceClient", lambda token, **kwargs: LocalVoiceClient(token, connect=connect, **kwargs)
    )
    monkeypatch.setattr(LabConfig, "from_environment", lambda: None)
    monkeypatch.setenv(module.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(module.sys, "argv", ["host", "probe", "--acknowledged"])
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "authentication_failed" and result["cloud_provider_calls"] == 2
    assert result["provider_active_stage"] == "asr" and result["host_connection_stage"] == "voice_turn"
    assert result["service_chain_completed"] is False and KEY not in json.dumps(result)
    assert len(sent) == 1


def test_doctor_cli_needs_no_inference_acknowledgement_or_api_key(monkeypatch, capsys):
    from app.sip_lab import LabConfig

    monkeypatch.setattr(LabConfig, "from_environment", lambda: None)
    monkeypatch.setenv(module.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(module.sys, "argv", ["host", "doctor"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("must not prompt"))
    monkeypatch.setattr(module, "doctor_host", lambda token: {"host_ready": True, "external_calls": 0})
    assert module.main() == 0 and json.loads(capsys.readouterr().out)["external_calls"] == 0
