import io
import json
import wave
from contextlib import contextmanager

import httpx
import pytest

from app import sip_lab_qwen_voice_probe as module
from app.sip_lab_qwen_probe import QwenProbeError

KEY = "sk-sp-SYNTHETIC_VOICE_TEST_ONLY_123456789"
PCM = b"\x01\x00" * 1600
REPLY = "语音测试确认。"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv(module.KEY_ENV, raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)


class SpeechSocket:
    def __init__(self, commands, *, failure=None, audio=PCM):
        self.commands = commands
        self.task = None
        self.events = []
        self.failure = failure
        self.audio = audio

    def send(self, raw):
        command = json.loads(raw)
        self.commands.append(command)
        if self.task is None:
            self.task = command["header"]["task_id"]
            self.events.append(self.event("task-started"))
        else:
            assert command["header"]["task_id"] == self.task
        if command["header"]["action"] == "finish-task":
            if self.failure:
                self.events.append(self.event(self.failure))
            else:
                self.events.extend([self.audio, self.event("task-finished")])

    def event(self, event):
        return json.dumps(
            {"header": {"event": event, "task_id": self.task, "error_message": KEY + " private provider details"}}
        )

    def recv(self, timeout):
        assert 0 < timeout <= 15
        return self.events.pop(0)


def harness(*, asr_response=None, llm_response=None, failure=None, audio=PCM, clock=None):
    commands, requests = [], []

    @contextmanager
    def ws(key, timeout):
        assert key == KEY and 0 < timeout <= 120
        yield SpeechSocket(commands, failure=failure, audio=audio)

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + KEY
        if str(request.url) == module.ASR_URL:
            return (
                asr_response
                if asr_response is not None
                else httpx.Response(200, json={"output": {"text": module.PHRASE}})
            )
        assert str(request.url) == module.CHAT
        return (
            llm_response
            if llm_response is not None
            else httpx.Response(
                200,
                json={
                    "model": module.MODELS[0],
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps({"reply": REPLY, "end": False})}}
                    ],
                    "usage": {"prompt_tokens": 105, "completion_tokens": 28},
                },
            )
        )

    kwargs = {"ws_factory": ws, "transport": httpx.MockTransport(handle)}
    if clock is not None:
        kwargs["clock"] = clock
    return module.QwenVoiceProbe(KEY, **kwargs), commands, requests


def test_fixed_subscription_chain_validates_audio_and_recognition_before_llm():
    probe, commands, requests = harness()
    result = probe.run()
    assert result["service_chain_completed"] and result["asr_verified"]
    assert result["llm_verified"] and result["tts_verified"]
    assert result["external_calls"] == 4 and len(requests) == 2 and len(commands) == 6
    assert result["completed_stages"] == ["source_tts", "asr", "llm", "reply_tts"]
    assert result["request_state"] == "completed" and result["first_token_ms"] is None
    assert not any(
        result[name] for name in ("phone_audio_verified", "business_ready", "billing_verified", "retry_performed")
    )
    for command in (commands[0], commands[3]):
        payload = command["payload"]
        assert payload["model"] == module.TTS
        assert payload["parameters"] == {
            "text_type": "PlainText",
            "voice": module.VOICE,
            "format": "pcm",
            "sample_rate": 8000,
        }
    assert commands[1]["payload"]["input"]["text"] == module.PHRASE
    assert commands[4]["payload"]["input"]["text"] == REPLY
    asr = json.loads(requests[0].content)
    uri = asr["input"]["messages"][0]["content"][0]["input_audio"]["data"]
    assert uri.startswith("data:audio/wav;base64,")
    with wave.open(io.BytesIO(module.base64.b64decode(uri.split(",", 1)[1])), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 8000)
        assert wav.readframes(wav.getnframes()) == PCM
    assert asr["model"] == module.ASR
    assert asr["parameters"] == {"format": "wav", "sample_rate": "8000", "language_hints": ["zh"]}
    llm = json.loads(requests[1].content)
    assert llm["model"] == module.MODELS[0] and llm["messages"][1]["content"] == module.PHRASE
    serialized = json.dumps(result, ensure_ascii=False)
    assert KEY not in serialized and module.PHRASE not in serialized and REPLY not in serialized
    with pytest.raises(QwenProbeError, match="voice_probe_already_run"):
        probe.run()
    assert len(requests) == 2


@pytest.mark.parametrize("text", ["今天是语音链路测试，请确认", "今天是语音链路测试。请确认！"])
def test_asr_ignores_only_punctuation_for_fixed_source(text):
    probe, _, _ = harness(asr_response=httpx.Response(200, json={"output": {"text": text}}))
    assert probe.run()["service_chain_completed"]


@pytest.mark.parametrize(
    "body,error",
    [
        ({"output": {"text": "今天不是语音测试"}}, "asr_synthetic_phrase_mismatch"),
        ({"output": {"text": KEY}}, "asr_synthetic_phrase_mismatch"),
        ({"output": {"text": 123}}, "invalid_asr_response"),
        ({"output": {}}, "invalid_asr_response"),
        ({"output": {"text": "x" * 121}}, "invalid_asr_response"),
    ],
)
def test_asr_mismatch_stops_before_llm_or_reply_tts(body, error):
    probe, commands, requests = harness(asr_response=httpx.Response(200, json=body))
    with pytest.raises(QwenProbeError, match=error):
        probe.run()
    result = probe.report(error)
    assert len(commands) == 3 and len(requests) == 1 and result["external_calls"] == 2
    assert result["completed_stages"] == ["source_tts"] and not result["service_chain_completed"]
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize(
    "code,error",
    [
        (401, "authentication_failed"),
        (403, "model_or_plan_denied"),
        (429, "quota_or_rate_limited"),
        (302, "redirect_denied"),
    ],
)
def test_asr_rejection_never_redirects_retries_or_exposes_provider(code, error):
    probe, _, requests = harness(
        asr_response=httpx.Response(code, headers={"Location": module.CHAT}, json={"message": KEY})
    )
    with pytest.raises(QwenProbeError, match=error):
        probe.run()
    assert len(requests) == 1 and probe.request_state == "rejected"
    assert KEY not in json.dumps(probe.report(error))


def test_asr_response_budget_stops_chain():
    probe, _, requests = harness(asr_response=httpx.Response(200, content=b"x" * 65537))
    with pytest.raises(QwenProbeError, match="asr_response_too_large"):
        probe.run()
    assert len(requests) == 1 and probe.external_calls == 2


@pytest.mark.parametrize(
    "audio,error",
    [(b"", "tts_incomplete_audio"), (b"x", "tts_incomplete_audio"), (b"x" * 160002, "tts_audio_budget_exceeded")],
)
def test_audio_bounds_stop_before_asr(audio, error):
    probe, _, requests = harness(audio=audio)
    with pytest.raises(QwenProbeError, match=error):
        probe.run()
    assert not requests and probe.external_calls == 1 and probe.metrics == []


def test_speech_failure_is_redacted_and_has_partial_diagnostics():
    probe, _, requests = harness(failure="task-failed")
    with pytest.raises(QwenProbeError, match="speech_task_failed"):
        probe.run()
    assert not requests and probe.request_state == "rejected"
    assert KEY not in json.dumps(probe.report("speech_task_failed"))


def test_llm_failure_stops_before_reply_tts_and_preserves_count():
    probe, commands, requests = harness(llm_response=httpx.Response(429, json={"error": KEY}))
    with pytest.raises(QwenProbeError, match="quota_or_rate_limited"):
        probe.run()
    assert len(commands) == 3 and len(requests) == 2 and probe.external_calls == 3
    assert probe.report()["completed_stages"] == ["source_tts", "asr"]


def test_deadline_stops_before_any_model_request():
    now = [0]
    probe, _, requests = harness(clock=lambda: now[0])
    now[0] = 121
    with pytest.raises(QwenProbeError, match="voice_probe_deadline_exceeded"):
        probe.run()
    assert not requests and probe.external_calls == 0


def test_explicit_playback_is_separate_from_chain_and_has_no_extra_calls(monkeypatch):
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    played = []
    monkeypatch.setattr(module, "play_audio", played.append)
    probe, _, _ = harness()
    result = probe.run(play=True)
    assert played == [PCM] and result["playback_state"] == "completed" and result["external_calls"] == 4
    assert result["phone_audio_verified"] is False


def test_status_and_missing_credential_have_zero_external_calls(monkeypatch, capsys):
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    monkeypatch.setattr(module.sys, "argv", ["probe", "status"])
    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["external_calls"] == 0
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY)
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(module.sys, "argv", ["probe", "probe", "--acknowledged"])
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "credential_missing_use_local_prompt" and result["external_calls"] == 0


@pytest.mark.parametrize(
    "production,arguments,error",
    [
        (False, ["probe"], "probe_acknowledgement_required"),
        (True, ["probe", "--acknowledged"], "production_probe_disabled"),
    ],
)
def test_cli_gates_before_prompt_or_service(production, arguments, error, monkeypatch, capsys):
    if production:
        monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(module.sys, "argv", ["probe", *arguments])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == error and result["external_calls"] == 0


def test_cli_hidden_prompt_and_exception_never_leak(monkeypatch, capsys):
    monkeypatch.setattr(module.sys, "argv", ["probe", "probe", "--acknowledged"])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda _: KEY)

    def fail(self, **kwargs):
        raise RuntimeError(KEY)

    monkeypatch.setattr(module.QwenVoiceProbe, "run", fail)
    assert module.main() == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["credential_source"] == "local_prompt" and result["error"] == "voice_probe_unavailable_or_failed"
    assert result["external_calls"] == 0 and KEY not in output


def test_fixed_websocket_factory_does_not_use_proxy_or_other_billing_endpoint(monkeypatch):
    calls = []

    @contextmanager
    def connect(url, **kwargs):
        calls.append((url, kwargs))
        yield object()

    monkeypatch.setattr("websockets.sync.client.connect", connect)
    with module.websocket(KEY, 10):
        pass
    assert calls[0][0] == "wss://token-plan.maas.qianwenaiapi.com/api-ws/v1/inference"
    assert calls[0][1]["proxy"] is None and calls[0][1]["open_timeout"] == 5


def test_real_loopback_websocket_binary_protocol_with_synthetic_provider():
    import threading

    from websockets.sync.client import connect
    from websockets.sync.server import serve

    connections = []

    def handler(connection):
        command = json.loads(connection.recv(timeout=2))
        connections.append(command)
        task = command["header"]["task_id"]
        connection.send(json.dumps({"header": {"event": "task-started", "task_id": task}}))
        for expected in ("continue-task", "finish-task"):
            command = json.loads(connection.recv(timeout=2))
            assert command["header"] == {"action": expected, "task_id": task, "streaming": "duplex"}
        connection.send(PCM)
        connection.send(json.dumps({"header": {"event": "task-finished", "task_id": task}}))

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        @contextmanager
        def local_socket(key, timeout):
            with connect(f"ws://127.0.0.1:{port}", proxy=None, open_timeout=2, close_timeout=1) as connection:
                yield connection

        probe, _, _ = harness()
        probe.ws_factory = local_socket
        try:
            result = probe.run()
        finally:
            server.shutdown()
            thread.join(timeout=3)
    assert len(connections) == 2 and result["service_chain_completed"]
    assert result["external_calls"] == 4 and not result["phone_audio_verified"]


def test_timeout_cli_preserves_attempt_count_without_retry(monkeypatch, capsys):
    probe, _, _ = harness()

    @contextmanager
    def timeout_socket(*args):
        raise TimeoutError(KEY)
        yield  # pragma: no cover

    probe.ws_factory = timeout_socket
    monkeypatch.setenv(module.KEY_ENV, KEY)
    monkeypatch.setattr(module.sys, "argv", ["probe", "probe", "--acknowledged"])
    monkeypatch.setattr(module, "QwenVoiceProbe", lambda _: probe)
    assert module.main() == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["external_calls"] == 1 and result["request_state"] == "unknown"
    assert result["error"] == "provider_timeout_result_unknown" and not result["retry_performed"]
    assert KEY not in output


def test_playback_temporary_wav_is_removed_and_never_contains_credentials(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    paths = []

    def run(arguments, **kwargs):
        assert arguments[0] == "/usr/bin/afplay" and kwargs["timeout"] == 15
        file = Path(arguments[1])
        paths.append(file)
        assert file.stat().st_mode & 0o777 == 0o600
        with wave.open(str(file), "rb") as audio:
            assert audio.readframes(audio.getnframes()) == PCM
        raise RuntimeError("synthetic playback failure")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        module.play_audio(PCM)
    assert paths and not paths[0].exists()
