"""Single-session Token Plan phone lab host. No customer dialogue or business tools."""

import argparse
import base64
import getpass
import importlib.metadata
import json
import os
import re
import sys
import time
import uuid

import httpx

from .sip_lab import LabConfig
from .sip_lab_qwen_probe import CASES, CHAT, KEY_ENV, MODELS, SYSTEM, QwenProbeError, normalize_key
from .sip_lab_qwen_voice_probe import (
    ASR,
    ASR_URL,
    MAX_AUDIO_BYTES,
    RATE,
    TTS,
    VOICE,
    QwenVoiceProbe,
    wav_bytes,
    websocket,
)
from .sip_lab_voice import (
    QWEN_HOST_URL,
    LocalVoiceClient,
    VoiceCancelled,
    VoiceError,
    digest,
    validate_reply,
)

TOKEN_ENV = "SIP_LAB_QWEN_VOICE_TOKEN"
MAX_CALLS = 24
SAFE_ERRORS = frozenset(
    {
        "qwen_session_call_budget_exceeded",
        "authentication_failed",
        "model_or_plan_denied",
        "quota_or_rate_limited",
        "redirect_denied",
        "provider_rejected",
        "provider_response_too_large",
        "provider_timeout_result_unknown",
        "provider_network_result_unknown",
        "invalid_provider_json",
        "invalid_phone_test_pcm",
        "invalid_asr_text",
        "invalid_asr_response",
        "invalid_test_dialogue",
        "invalid_test_history",
        "llm_incomplete",
        "reported_model_mismatch",
        "invalid_usage",
        "end_intent_mismatch",
        "numbers_date_mismatch",
        "invalid_model_json",
        "invalid_reply_shape",
        "invalid_reply_value",
        "off_scope_reply",
        "invalid_reply_control",
        "invalid_speech_event",
        "speech_task_mismatch",
        "speech_task_failed",
        "unexpected_speech_event",
        "speech_task_not_started",
        "tts_audio_budget_exceeded",
        "tts_incomplete_audio",
        "speech_provider_unavailable_result_unknown",
        "asr_probe_phrase_mismatch",
        "turn_deadline_exceeded",
        "invalid_tts_chunk",
        "empty_tts_audio",
        "qwen_host_inference_failed",
    }
)
GREETING = "这里是千问语音测试，请说固定测试短句。说结束测试即可结束。"
SCOPE_REPLY = "这里只进行固定语音测试，请说今天是语音链路测试。"
STOP_REPLY = "测试结束，再见。"


class HostProbeError(VoiceError):
    def __init__(self, code, diagnostics):
        super().__init__(code)
        self.diagnostics = diagnostics


def websocket_dependencies():
    version = None
    try:
        version = importlib.metadata.version("websockets")
        from websockets.sync.client import reconnect

        compatible = isinstance(reconnect, type) and callable(getattr(reconnect, "process_redirect", None))
    except ImportError:
        compatible = False
    return {
        "websockets_version": version
        if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,30}", version)
        else None,
        "websockets_required": "17.2",
        "websocket_no_redirect_api_available": compatible,
    }


def doctor_host(token, *, transport=None):
    report = {
        "mode": "qwen_voice_host_doctor",
        "host": QWEN_HOST_URL,
        **websocket_dependencies(),
        "host_reachable": False,
        "host_authenticated": False,
        "host_ready": False,
        "external_calls": 0,
        "phone_audio_verified": False,
        "business_ready": False,
    }
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False, timeout=3) as client:
            with client.stream(
                "GET", "http://127.0.0.1:8092/lab/status", headers={"Authorization": "Bearer " + token}
            ) as response:
                report["host_reachable"] = True
                if response.status_code != 200:
                    raise VoiceError(
                        "qwen_host_auth_failed" if response.status_code == 403 else "qwen_host_status_rejected"
                    )
                report["host_authenticated"] = True
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    if len(body) + len(chunk) > 4096:
                        raise VoiceError("invalid_qwen_host_status")
                    body.extend(chunk)
                data = json.loads(body)
        provider = validate_provider(data.get("provider"))
        if any(type(data.get(name)) is not bool for name in ("busy", "draining", "warmed")):
            raise VoiceError("invalid_qwen_host_status")
        report.update(provider=provider, host_busy=data["busy"], host_draining=data["draining"])
        if not report["websocket_no_redirect_api_available"]:
            raise VoiceError("websocket_dependency_incompatible")
        if data["busy"]:
            raise VoiceError("qwen_host_busy")
        if data["draining"] or not data["warmed"]:
            raise VoiceError("qwen_host_not_ready")
        report.update(host_ready=True, error=None)
    except VoiceError as exc:
        report["error"] = str(exc)
    except httpx.TimeoutException:
        report["error"] = "qwen_host_timeout"
    except httpx.ConnectError:
        report["error"] = "qwen_host_connection_failed"
    except httpx.HTTPError:
        report["error"] = "qwen_host_network_failed"
    except (ValueError, TypeError, AttributeError):
        report["error"] = "invalid_qwen_host_status"
    return report


def provider_configuration():
    return {
        "kind": "qwen_token_plan",
        "models": {"asr": ASR, "llm": MODELS[0], "tts": TTS},
        "voice": VOICE,
        "sample_rate": RATE,
        "protocol_version": 1,
    }


def validate_provider(data):
    if not isinstance(data, dict) or data != provider_configuration():
        raise VoiceError("qwen_voice_provider_mismatch")
    # Dict equality treats booleans as integers; wire types must still be exact.
    if type(data["sample_rate"]) is not int or type(data["protocol_version"]) is not int:
        raise VoiceError("qwen_voice_provider_mismatch")
    return data


def connection_token():
    from .sip_lab_voice import TOKEN

    token = os.getenv(TOKEN_ENV, "")
    if not TOKEN.fullmatch(token):
        raise VoiceError("invalid_qwen_voice_token")
    return token


def normalized(text):
    return re.sub(r"[\W_]", "", text).replace("一二三四", "1234").replace("十月九日", "10月9日")


def test_case(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 120:
        raise VoiceError("invalid_asr_text")
    value = normalized(text)
    aliases = {
        "confirm": (CASES["confirm"][0], "今天是语音链路测试", "换一句测试短句"),
        "numbers_date": (
            CASES["numbers_date"][0],
            "请复述测试数字1234和测试日期10月9日",
            "请复述数字1234和日期10月9日",
        ),
        "negation": (CASES["negation"][0], "不要结束测试", "我不是要结束测试", "继续测试"),
        "stop": (CASES["stop"][0], "结束测试", "请结束测试", "停止测试", "请停止测试", "再见"),
    }
    for case, phrases in aliases.items():
        if value in {normalized(phrase) for phrase in phrases}:
            return case
    return None


class TokenVoiceEngine:
    def __init__(self, key, *, transport=None, ws_factory=websocket, clock=time.monotonic):
        if os.getenv("APP_ENV") == "production":
            raise VoiceError("production_probe_disabled")
        try:
            self.key = normalize_key(key)
        except QwenProbeError as exc:
            raise VoiceError(str(exc)) from None
        self.transport, self.ws_factory, self.clock = transport, ws_factory, clock
        self.lab_provider = provider_configuration()
        self.warmed = True  # Host protocol readiness only; never an inference verification.
        self.begin_session()

    def begin_session(self):
        self.external_calls = 0
        self.request_state = "not_sent"
        self.active_stage = None
        self.last_error = None

    def diagnostics(self):
        return {
            "provider": self.lab_provider,
            "cloud_provider_calls": self.external_calls,
            "provider_request_state": self.request_state,
            "provider_error": self.last_error,
            "provider_active_stage": self.active_stage,
        }

    def record_error(self, exc):
        code = str(exc) if isinstance(exc, VoiceError) else None
        self.last_error = code if code in SAFE_ERRORS else "qwen_host_inference_failed"

    def begin(self, stage, check):
        check()
        if self.external_calls >= MAX_CALLS:
            raise VoiceError("qwen_session_call_budget_exceeded")
        self.external_calls += 1
        self.active_stage = stage
        self.request_state = "unknown"
        self.last_error = None

    def http_json(self, url, payload, stage, check):
        self.begin(stage, check)
        try:
            with httpx.Client(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(3, connect=3)
            ) as client:
                with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": "Bearer " + self.key, "X-DashScope-SSE": "disable"},
                    json=payload,
                ) as response:
                    check()
                    if response.status_code != 200:
                        self.request_state = "rejected"
                        error = {
                            401: "authentication_failed",
                            403: "model_or_plan_denied",
                            429: "quota_or_rate_limited",
                        }.get(
                            response.status_code,
                            "redirect_denied" if 300 <= response.status_code < 400 else "provider_rejected",
                        )
                        raise VoiceError(error)
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        check()
                        if len(body) + len(chunk) > 65536:
                            raise VoiceError("provider_response_too_large")
                        body.extend(chunk)
                    self.request_state = "completed"
            check()
            return json.loads(body)
        except VoiceCancelled:
            raise
        except VoiceError as exc:
            self.last_error = str(exc)
            raise
        except httpx.TimeoutException:
            self.last_error = "provider_timeout_result_unknown"
            raise VoiceError(self.last_error) from None
        except httpx.HTTPError:
            self.last_error = "provider_network_result_unknown"
            raise VoiceError(self.last_error) from None
        except (ValueError, TypeError):
            self.last_error = "invalid_provider_json"
            raise VoiceError(self.last_error) from None

    def asr(self, pcm, check):
        if not isinstance(pcm, bytes) or not 3200 <= len(pcm) <= 96000 or len(pcm) % 2:
            raise VoiceError("invalid_phone_test_pcm")
        uri = "data:audio/wav;base64," + base64.b64encode(wav_bytes(pcm)).decode("ascii")
        data = self.http_json(
            ASR_URL,
            {
                "model": ASR,
                "input": {
                    "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": uri}}]}]
                },
                "parameters": {"format": "wav", "sample_rate": str(RATE), "language_hints": ["zh"]},
            },
            "asr",
            check,
        )
        try:
            text = data["output"]["text"]
            if not isinstance(text, str) or not 1 <= len(text) <= 120:
                raise VoiceError("invalid_asr_text")
            return text
        except (KeyError, TypeError):
            raise VoiceError("invalid_asr_response") from None

    def reply(self, case, history, check):
        if case not in CASES or case == "stop" or len(history) > 8 or len(history) % 2:
            raise VoiceError("invalid_test_dialogue")
        for index, row in enumerate(history):
            role = "user" if index % 2 == 0 else "assistant"
            if not isinstance(row, dict) or set(row) != {"role", "content"} or row["role"] != role:
                raise VoiceError("invalid_test_history")
            if role == "user" and row["content"] not in [value[0] for value in CASES.values()]:
                raise VoiceError("invalid_test_history")
            if role == "assistant":
                validate_reply({"reply": row["content"], "end": False})
        data = self.http_json(
            CHAT,
            {
                "model": MODELS[0],
                "enable_thinking": False,
                "stream": False,
                "max_tokens": 128,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    *history,
                    {"role": "user", "content": CASES[case][0]},
                ],
            },
            "llm",
            check,
        )
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise VoiceError("llm_incomplete")
            reported = data.get("model")
            if reported is not None and (
                not isinstance(reported, str)
                or not re.fullmatch(re.escape(MODELS[0]) + r"(?:-[A-Za-z0-9._-]{1,60})?", reported)
            ):
                raise VoiceError("reported_model_mismatch")
            usage = data.get("usage")
            if usage is not None and (
                not isinstance(usage, dict)
                or any(
                    type(usage.get(name)) is not int or not 0 <= usage[name] <= 100000
                    for name in ("prompt_tokens", "completion_tokens")
                )
                or usage["completion_tokens"] > 128
            ):
                raise VoiceError("invalid_usage")
            reply, end = validate_reply(json.loads(choice["message"]["content"]))
            if end is not False:
                raise VoiceError("end_intent_mismatch")
            if case == "numbers_date" and not all(value in reply for value in ("1234", "10月9日")):
                raise VoiceError("numbers_date_mismatch")
            return reply, end
        except (ValueError, KeyError, IndexError, TypeError):
            raise VoiceError("invalid_model_json") from None

    def speech_event(self, connection, task, check):
        while True:
            check()
            try:
                raw = connection.recv(timeout=0.05)
                break
            except TimeoutError:
                continue
        check()
        if isinstance(raw, bytes):
            return raw
        try:
            if not isinstance(raw, str) or len(raw) > 65536:
                raise VoiceError("invalid_speech_event")
            data = json.loads(raw)
            header = data["header"]
            if header["task_id"] != task:
                raise VoiceError("speech_task_mismatch")
            if header.get("event") == "task-failed":
                self.request_state = "rejected"
                raise VoiceError("speech_task_failed")
            if header.get("event") not in {"task-started", "result-generated", "task-finished"}:
                raise VoiceError("unexpected_speech_event")
            return data
        except (ValueError, KeyError, TypeError):
            raise VoiceError("invalid_speech_event") from None

    def tts(self, text, check):
        validate_reply({"reply": text, "end": False})
        self.begin("tts", check)
        task = str(uuid.uuid4())
        total, pending = 0, b""
        try:
            with self.ws_factory(self.key, 3) as connection:
                check()
                connection.send(
                    QwenVoiceProbe.command(
                        "run-task",
                        task,
                        {
                            "task_group": "audio",
                            "task": "tts",
                            "function": "SpeechSynthesizer",
                            "model": TTS,
                            "parameters": {
                                "text_type": "PlainText",
                                "voice": VOICE,
                                "format": "pcm",
                                "sample_rate": RATE,
                            },
                            "input": {},
                        },
                    )
                )
                event = self.speech_event(connection, task, check)
                if not isinstance(event, dict) or event["header"]["event"] != "task-started":
                    raise VoiceError("speech_task_not_started")
                connection.send(QwenVoiceProbe.command("continue-task", task, {"input": {"text": text}}))
                connection.send(QwenVoiceProbe.command("finish-task", task, {"input": {}}))
                finished = False
                for _ in range(256):
                    event = self.speech_event(connection, task, check)
                    if isinstance(event, bytes):
                        total += len(event)
                        if total > MAX_AUDIO_BYTES:
                            raise VoiceError("tts_audio_budget_exceeded")
                        pending += event
                        size = len(pending) - len(pending) % 2
                        for offset in range(0, size, 3200):
                            check()
                            yield pending[offset : min(offset + 3200, size)]
                        pending = pending[size:]
                    elif event["header"]["event"] == "task-finished":
                        self.request_state = "completed"
                        finished = True
                        break
                if not finished or not total or pending:
                    raise VoiceError("tts_incomplete_audio")
                check()
        except VoiceCancelled:
            raise
        except VoiceError as exc:
            self.last_error = str(exc)
            raise
        except Exception:
            self.last_error = "speech_provider_unavailable_result_unknown"
            raise VoiceError(self.last_error) from None


def execute_qwen_turn(engine, kind, pcm, history, cancel, emit, clock=time.monotonic):
    start = clock()
    first = None
    stages = {"source_tts": 0, "asr": 0, "llm": 0, "reply_tts": 0}

    def check():
        if cancel.is_set():
            raise VoiceCancelled("turn_cancelled")
        if clock() - start >= 20:
            raise VoiceError("turn_deadline_exceeded")

    check()
    case, saved_history = None, []
    if kind == "greeting":
        reply, end = GREETING, False
    else:
        if kind == "probe":
            stage = clock()
            pcm = b"".join(engine.tts(CASES["confirm"][0], check))
            stages["source_tts"] = round((clock() - stage) * 1000)
        stage = clock()
        text = engine.asr(pcm, check)
        stages["asr"] = round((clock() - stage) * 1000)
        if kind == "probe" and normalized(text) != normalized(CASES["confirm"][0]):
            raise VoiceError("asr_probe_phrase_mismatch")
        case = test_case(text)
        if case == "stop":
            reply, end = STOP_REPLY, True
        elif case is None:
            reply, end = SCOPE_REPLY, False
        else:
            stage = clock()
            reply, end = engine.reply(case, list(history), check)
            stages["llm"] = round((clock() - stage) * 1000)
            saved_history = [{"role": "user", "content": CASES[case][0]}, {"role": "assistant", "content": reply}]
    validate_reply({"reply": reply, "end": end})
    stage = clock()
    count = 0
    for block in engine.tts(reply, check):
        check()
        if not isinstance(block, bytes) or not block or len(block) % 2 or len(block) > 3200:
            raise VoiceError("invalid_tts_chunk")
        count += len(block)
        if count > MAX_AUDIO_BYTES:
            raise VoiceError("tts_audio_budget_exceeded")
        if first is None:
            first = clock()
        emit(block)
    check()
    if not count:
        raise VoiceError("empty_tts_audio")
    stages["reply_tts"] = round((clock() - stage) * 1000)
    return {
        "end": end,
        "reply_digest": digest(reply),
        "elapsed_ms": round((clock() - start) * 1000),
        "first_audio_ms": round((first - start) * 1000),
        "stage_ms": stages,
        "_history": saved_history,
    }


def probe_host(token):
    voice = LocalVoiceClient(token, url=QWEN_HOST_URL, expected_provider="qwen_token_plan")
    voice.start(kind="probe")
    samples = 0
    try:
        until = time.monotonic() + 25
        while time.monotonic() < until:
            block = voice.pop()
            if block:
                samples += len(block) // 2
            elif voice.completed:
                break
            if voice.failed:
                raise VoiceError(voice.error_code or "qwen_host_probe_failed")
            time.sleep(0.01)
        if voice.completed != 1 or not samples or voice.failed:
            raise VoiceError("qwen_host_probe_incomplete")
        return {
            "mode": "qwen_host_synthetic_probe",
            "service_chain_completed": True,
            "asr_phrase_matched": True,
            "tts_samples": samples,
            **voice.summary(),
            "phone_audio_verified": False,
            "business_ready": False,
            "billing_verified": False,
        }
    except VoiceError as exc:
        raise HostProbeError(
            str(exc), {**voice.summary(), "tts_samples_received": samples, "service_chain_completed": False}
        ) from None
    finally:
        voice.close()


def main():
    parser = argparse.ArgumentParser(description="本机千问套餐电话测试宿主；固定8092端口、短句与合成对话。")
    parser.add_argument("action", choices=["status", "doctor", "serve", "probe"])
    parser.add_argument("--acknowledged", action="store_true")
    args = parser.parse_args()
    stage = "configuration"
    try:
        if args.action == "status":
            print(
                json.dumps(
                    {
                        "mode": "qwen_voice_configuration",
                        "provider": provider_configuration(),
                        "host": QWEN_HOST_URL,
                        "external_calls": 0,
                        **websocket_dependencies(),
                        "business_ready": False,
                        "phone_audio_verified": False,
                    }
                )
            )
            return 0
        if args.action != "doctor" and not args.acknowledged:
            raise VoiceError("qwen_voice_acknowledgement_required")
        LabConfig.from_environment()
        token = connection_token()
        if args.action == "doctor":
            report = doctor_host(token)
            print(json.dumps(report))
            return 0 if report["host_ready"] else 2
        stage = "dependency_check"
        if not websocket_dependencies()["websocket_no_redirect_api_available"]:
            raise VoiceError("websocket_dependency_incompatible")
        if args.action == "probe":
            stage = "synthetic_probe"
            print(json.dumps(probe_host(token)))
            return 0
        key = os.getenv(KEY_ENV, "")
        if not key:
            if not sys.stdin.isatty():
                raise VoiceError("credential_missing_use_local_prompt")
            key = getpass.getpass("Token Plan 完整 API Key（只存本机进程内存，不回显、不保存）：")
        stage = "host_start"
        engine = TokenVoiceEngine(key)
        import uvicorn

        from .sip_lab_local_voice import create_app

        print(
            json.dumps(
                {
                    "event": "qwen_voice_ready",
                    "provider": engine.lab_provider,
                    "host": QWEN_HOST_URL,
                    **websocket_dependencies(),
                    "provider_verified": False,
                    "business_ready": False,
                }
            ),
            flush=True,
        )
        uvicorn.run(
            create_app(engine, token, turn_executor=execute_qwen_turn),
            host="127.0.0.1",
            port=8092,
            access_log=False,
            log_level="critical",
            ws_max_size=100000,
            ws_max_queue=8,
        )
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(
            json.dumps(
                {
                    "event": "qwen_voice_unavailable",
                    "stage": stage,
                    "error": str(exc) if isinstance(exc, VoiceError) else "qwen_host_unavailable_or_failed",
                    **(exc.diagnostics if isinstance(exc, HostProbeError) else {}),
                    **websocket_dependencies(),
                    "phone_audio_verified": False,
                    "business_ready": False,
                }
            ),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
