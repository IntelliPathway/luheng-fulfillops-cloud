import json

import httpx
import pytest

from app import sip_lab_qwen_probe as module
from app.sip_lab_qwen_probe import CASES, KEY_ENV, MODELS, QwenProbeError, QwenTokenProbe, main

KEY = "sk-sp-SYNTHETIC_TEST_ONLY_" + "x" * 24


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)


def response(model=MODELS[0], reply="语音测试确认。", end=False, **overrides):
    body = {
        "model": model,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"reply": reply, "end": end}, ensure_ascii=False)},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def test_two_model_suite_uses_exact_subscription_endpoint_and_synthetic_cases():
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer " + KEY
        payload = json.loads(request.content)
        assert payload["model"] in MODELS
        assert payload["enable_thinking"] is False and payload["stream"] is False
        assert payload["max_tokens"] == 128 and payload["response_format"] == {"type": "json_object"}
        assert set(payload) == {"model", "enable_thinking", "stream", "max_tokens", "response_format", "messages"}
        assert payload["messages"][0] == {"role": "system", "content": module.SYSTEM}
        phrase = payload["messages"][1]["content"]
        assert phrase in [case[0] for case in CASES.values()]
        case_id = next(name for name, value in CASES.items() if value[0] == phrase)
        reply = "测试数字1234，测试日期10月9日。" if case_id == "numbers_date" else "语音测试确认。"
        return response(model=payload["model"], reply=reply, end=CASES[case_id][1])

    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(handle))
    result = probe.run(MODELS, tuple(CASES))
    assert len(requests) == result["external_calls"] == len(result["results"]) == 8
    assert result["llm_verified"] is True and result["request_state"] == "completed"
    assert result["first_token_ms"] is None and result["timing_scope"] == "nonstream_http_complete_response"
    assert not any(
        result[field]
        for field in (
            "business_ready",
            "phone_audio_verified",
            "service_chain_completed",
            "billing_verified",
            "retry_performed",
        )
    )
    assert all(row["status"] == "passed" and len(row["reply_digest"]) == 64 for row in result["results"])
    serialized = json.dumps(result, ensure_ascii=False)
    assert KEY not in serialized and "语音测试确认" not in serialized and "1234" not in serialized
    with pytest.raises(QwenProbeError, match="probe_call_budget_exceeded"):
        probe.call(MODELS[0], "confirm")
    assert len(requests) == 8


@pytest.mark.parametrize(
    "code,error",
    [
        (401, "authentication_failed"),
        (403, "model_or_plan_denied"),
        (429, "quota_or_rate_limited"),
        (503, "provider_rejected"),
        (302, "redirect_denied"),
    ],
)
def test_rejected_requests_never_follow_redirect_retry_or_disclose_provider(code, error):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            code,
            headers={"Location": "https://dashscope.aliyuncs.com/private"},
            json={"error": {"message": KEY + " private provider details"}},
        )

    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(handle))
    with pytest.raises(QwenProbeError, match=error):
        probe.run(MODELS, tuple(CASES))
    assert len(requests) == probe.external_calls == 1 and probe.request_state == "rejected"
    assert probe.results == []
    assert KEY not in json.dumps(probe.report(False, error))


@pytest.mark.parametrize(
    "exception,error",
    [(httpx.ReadTimeout, "provider_timeout_result_unknown"), (httpx.ConnectError, "provider_network_result_unknown")],
)
def test_unknown_result_stops_without_inference_retry(exception, error):
    requests = []

    def handle(request):
        requests.append(request)
        raise exception(KEY, request=request)

    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(handle))
    with pytest.raises(QwenProbeError, match=error):
        probe.run(MODELS, tuple(CASES))
    assert len(requests) == 1 and probe.request_state == "unknown"


@pytest.mark.parametrize(
    "body,error",
    [
        (response(end=True), "end_intent_mismatch"),
        (response(reply="本金说明"), "invalid_model_json"),
        (response(reply="x" * 61), "invalid_model_json"),
        (response(end=0), "invalid_model_json"),
        (response(model="other-model"), "reported_model_mismatch"),
        (response(model=KEY), "reported_model_mismatch"),
        (response(usage={"prompt_tokens": True, "completion_tokens": 20}), "invalid_usage"),
        (response(usage={"prompt_tokens": 100, "completion_tokens": 129}), "output_token_budget_exceeded"),
        (response(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]), "llm_incomplete"),
        (response(choices=[]), "invalid_model_json"),
        (response(choices=[{"finish_reason": "stop", "message": {"content": "not json"}}]), "invalid_model_json"),
        (httpx.Response(200, content=b"x" * 65537), "response_too_large"),
    ],
)
def test_response_integrity_and_output_bounds(body, error):
    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(lambda request: body))
    with pytest.raises(QwenProbeError, match=error):
        probe.run((MODELS[0],), ("confirm",))
    assert probe.external_calls == 1 and probe.results == []


def test_numeric_date_and_negation_intents_are_verified():
    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(lambda request: response()))
    with pytest.raises(QwenProbeError, match="numbers_date_mismatch"):
        probe.call(MODELS[0], "numbers_date")
    wrong = QwenTokenProbe(KEY, transport=httpx.MockTransport(lambda request: response(end=True)))
    with pytest.raises(QwenProbeError, match="end_intent_mismatch"):
        wrong.call(MODELS[0], "negation")


def test_missing_model_or_usage_is_explicitly_unavailable():
    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(lambda request: response(model=None, usage=None)))
    result = probe.call(MODELS[0], "confirm")
    assert result["reported_model"] is None and result["usage"] is None


@pytest.mark.parametrize(
    "models,cases",
    [
        (("arbitrary-model",), ("confirm",)),
        ((MODELS[0], MODELS[0]), ("confirm",)),
        ((MODELS[0],), ("customer-data",)),
        ((MODELS[0],), ("confirm", "confirm")),
        ((), ("confirm",)),
    ],
)
def test_invalid_selection_never_calls_a_service(models, cases):
    probe = QwenTokenProbe(KEY, transport=httpx.MockTransport(lambda request: pytest.fail("unexpected request")))
    with pytest.raises(QwenProbeError, match="invalid_probe_selection"):
        probe.run(models, cases)
    assert probe.external_calls == 0


def test_status_never_prompts_or_calls_provider(monkeypatch, capsys):
    monkeypatch.setenv(KEY_ENV, KEY)
    monkeypatch.setattr("sys.argv", ["qwen-probe", "status"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    monkeypatch.setattr(QwenTokenProbe, "run", lambda *args: pytest.fail("unexpected inference"))
    assert main() == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["credential_state"] == "configured" and data["external_calls"] == 0
    assert data["endpoint"] == "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
    assert data["llm_verified"] is False and KEY not in output


@pytest.mark.parametrize(
    "command,error",
    [
        (["probe"], "probe_acknowledgement_required"),
        (["probe", "--acknowledged"], "credential_missing_use_local_prompt"),
    ],
)
def test_cli_gates_before_key_or_network(command, error, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["qwen-probe", *command])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY)
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    assert main() == 2
    data = json.loads(capsys.readouterr().out)
    assert data["error"] == error and data["external_calls"] == 0


def test_cli_local_hidden_prompt_and_default_single_model(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["qwen-probe", "probe", "--acknowledged"])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda _: KEY)
    original = QwenTokenProbe.__init__
    requests = []

    def initialize(self, key):
        def handle(request):
            requests.append(request)
            return response()

        original(self, key, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(QwenTokenProbe, "__init__", initialize)
    assert main() == 0
    output = capsys.readouterr().out
    assert KEY not in output and len(requests) == 1
    result = json.loads(output)
    assert result["llm_verified"] is True
    assert result["credential_source"] == "local_prompt" and result["credential_state"] == "configured"


def test_cli_exception_does_not_expose_key_and_production_never_prompts(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["qwen-probe", "probe", "--acknowledged"])
    monkeypatch.setenv(KEY_ENV, KEY)

    def fail(*args):
        raise RuntimeError(KEY + " provider request")

    monkeypatch.setattr(QwenTokenProbe, "run", fail)
    assert main() == 2
    output = capsys.readouterr().out
    assert KEY not in output and json.loads(output)["error"] == "qwen_probe_failed"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("unexpected prompt"))
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["error"] == "production_probe_disabled"
    with pytest.raises(QwenProbeError, match="production_probe_disabled"):
        QwenTokenProbe(KEY)


@pytest.mark.parametrize("copied", ["  " + KEY + "\n", '"' + KEY + '"', "'" + KEY + "'"])
def test_copied_key_is_normalized_before_fixed_endpoint_authentication(copied):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert str(request.url) == module.CHAT
        return response()

    result = QwenTokenProbe(copied, transport=httpx.MockTransport(handle)).run((MODELS[0],), ("confirm",))
    assert result["llm_verified"] and len(requests) == 1
    assert KEY not in json.dumps(result)


def test_provider_authenticates_opaque_key_without_client_prefix_or_length_assumptions():
    key = "SYNTHETIC+opaque/token=="
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + key
        assert str(request.url) == module.CHAT
        return httpx.Response(401, json={"error": {"message": key}})

    probe = QwenTokenProbe(key, transport=httpx.MockTransport(handle))
    with pytest.raises(QwenProbeError, match="authentication_failed"):
        probe.run((MODELS[0],), ("confirm",))
    assert len(requests) == 1 and probe.request_state == "rejected"
    assert key not in json.dumps(probe.report(False, "authentication_failed"))


@pytest.mark.parametrize(
    "key,error",
    [
        ("", "credential_missing"),
        ("  ", "credential_missing"),
        (module.BASE_URL, "credential_is_url"),
        ("sk-sp-****", "credential_is_masked_or_placeholder"),
        ("YOUR_API_KEY", "credential_is_masked_or_placeholder"),
        (KEY + " inside", "credential_contains_whitespace_or_control"),
        (KEY + "\0", "credential_contains_whitespace_or_control"),
        ("Bearer " + KEY, "credential_is_authorization_header"),
        ("中文", "credential_contains_non_ascii"),
        ("x" * 1025, "credential_too_long"),
    ],
)
def test_invalid_prompt_has_precise_source_and_zero_service_requests(key, error, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["qwen-probe", "probe", "--acknowledged"])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda _: key)
    monkeypatch.setattr(QwenTokenProbe, "run", lambda *args: pytest.fail("unexpected request"))
    assert main() == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["credential_source"] == "local_prompt"
    assert result["credential_state"] == ("missing" if error == "credential_missing" else "invalid")
    assert result["error"] == error and result["request_state"] == "not_sent" and result["external_calls"] == 0
    assert KEY not in output


def test_invalid_environment_key_reports_environment_source(monkeypatch, capsys):
    monkeypatch.setenv(KEY_ENV, KEY + " inside")
    monkeypatch.setattr("sys.argv", ["qwen-probe", "probe", "--acknowledged"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _: pytest.fail("must not replace configured credential"))
    assert main() == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["credential_source"] == "environment" and result["credential_state"] == "invalid"
    assert result["external_calls"] == 0 and KEY not in output
