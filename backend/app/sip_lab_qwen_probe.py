"""Explicit, bounded Token Plan LLM experiment; separate from phone and business APIs."""

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime

import httpx

from .sip_lab_voice import VoiceError, validate_reply

BASE_URL = "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
CHAT = BASE_URL + "/chat/completions"
MODELS = ("qwen3.8-flash", "qwen3.8-max")
KEY_ENV = "QWEN_TOKEN_PLAN_API_KEY"
MAX_KEY_LENGTH = 1024
SYSTEM = (
    "你是内部语音测试助手。只交流语音测试，复述测试数字和日期，不索取私人资料。"
    "只输出 JSON，只有 reply（最多60字）和 end（布尔值）两个字段。"
    "仅在用户明确要求结束时 end=true；否则 end=false。"
    "复述数字和日期时保留用户输入的阿拉伯数字格式，不输出 Markdown 或思考过程。"
)
CASES = {
    "confirm": ("今天是语音链路测试，请确认。", False),
    "numbers_date": ("请复述测试数字1234和测试日期10月9日，不要结束。", False),
    "negation": ("我不是要结束，请继续测试。", False),
    "stop": ("测试结束，请停止。", True),
}
BOUNDARY = {
    "phone_audio_verified": False,
    "service_chain_completed": False,
    "business_ready": False,
    "billing_verified": False,
}


class QwenProbeError(RuntimeError):
    pass


def normalize_key(value):
    if not isinstance(value, str):
        raise QwenProbeError("credential_missing")
    key = value.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
        key = key[1:-1].strip()
    if not key:
        raise QwenProbeError("credential_missing")
    if len(key) > MAX_KEY_LENGTH:
        raise QwenProbeError("credential_too_long")
    if key.lower().startswith(("https://", "http://")):
        raise QwenProbeError("credential_is_url")
    if key.lower().startswith("bearer "):
        raise QwenProbeError("credential_is_authorization_header")
    if "*" in key or key in {"YOUR_API_KEY", "your_api_key", "sk-sp-xxxxx"} or "…" in key or "..." in key:
        raise QwenProbeError("credential_is_masked_or_placeholder")
    if not key.isascii():
        raise QwenProbeError("credential_contains_non_ascii")
    if any(character.isspace() or ord(character) < 33 or ord(character) == 127 for character in key):
        raise QwenProbeError("credential_contains_whitespace_or_control")
    return key


def credential_state(key):
    try:
        normalize_key(key)
        return "configured"
    except QwenProbeError as exc:
        return "missing" if str(exc) == "credential_missing" else "invalid"


def status(key=None, source=None):
    if key is None:
        key = os.getenv(KEY_ENV, "")
        source = "environment" if key else "none"
    return {
        "mode": "qwen_token_plan_llm_probe",
        "credential_state": credential_state(key),
        "credential_source": source,
        "endpoint": CHAT,
        "models": list(MODELS),
        "external_calls": 0,
        "production_blocked": os.getenv("APP_ENV") == "production",
        "llm_verified": False,
        **BOUNDARY,
    }


class QwenTokenProbe:
    def __init__(self, key, *, transport=None, clock=time.monotonic):
        if os.getenv("APP_ENV") == "production":
            raise QwenProbeError("production_probe_disabled")
        self.key, self.transport, self.clock = normalize_key(key), transport, clock
        self.credential_source = "direct"
        self.results = []
        self.external_calls = 0
        self.request_state = "not_sent"
        self.active_model = None
        self.active_case = None

    def call(self, model, case_id):
        if model not in MODELS or case_id not in CASES:
            raise QwenProbeError("invalid_probe_selection")
        if self.external_calls >= 8:
            raise QwenProbeError("probe_call_budget_exceeded")
        self.active_model, self.active_case = model, case_id
        phrase, expected_end = CASES[case_id]
        payload = {
            "model": model,
            "enable_thinking": False,
            "stream": False,
            "max_tokens": 128,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": phrase}],
        }
        started = self.clock()
        self.external_calls += 1
        self.request_state = "unknown"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                with client.stream(
                    "POST", CHAT, headers={"Authorization": "Bearer " + self.key}, json=payload
                ) as response:
                    if response.status_code != 200:
                        self.request_state = "rejected"
                        code = {
                            401: "authentication_failed",
                            403: "model_or_plan_denied",
                            429: "quota_or_rate_limited",
                        }.get(
                            response.status_code,
                            "redirect_denied" if 300 <= response.status_code < 400 else "provider_rejected",
                        )
                        raise QwenProbeError(code)
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if self.clock() - started > 30:
                            raise QwenProbeError("response_deadline_exceeded")
                        if len(body) + len(chunk) > 65536:
                            raise QwenProbeError("response_too_large")
                        body.extend(chunk)
                    self.request_state = "completed"
        except httpx.TimeoutException:
            raise QwenProbeError("provider_timeout_result_unknown") from None
        except httpx.HTTPError:
            raise QwenProbeError("provider_network_result_unknown") from None
        elapsed = round((self.clock() - started) * 1000)
        try:
            data = json.loads(body)
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise QwenProbeError("llm_incomplete")
            reply, end = validate_reply(json.loads(choice["message"]["content"]))
            if end is not expected_end:
                raise QwenProbeError("end_intent_mismatch")
            if case_id == "numbers_date" and not all(value in reply for value in ("1234", "10月9日")):
                raise QwenProbeError("numbers_date_mismatch")
            reported_model = data.get("model")
            if reported_model is not None and (
                not isinstance(reported_model, str)
                or not re.fullmatch(re.escape(model) + r"(?:-[A-Za-z0-9._-]{1,60})?", reported_model)
            ):
                raise QwenProbeError("reported_model_mismatch")
            usage = data.get("usage")
            tokens = None
            if usage is not None:
                if not isinstance(usage, dict) or any(
                    type(usage.get(name)) is not int or not 0 <= usage[name] <= 100000
                    for name in ("prompt_tokens", "completion_tokens")
                ):
                    raise QwenProbeError("invalid_usage")
                if usage["completion_tokens"] > 128:
                    raise QwenProbeError("output_token_budget_exceeded")
                tokens = {name: usage[name] for name in ("prompt_tokens", "completion_tokens")}
        except (ValueError, KeyError, IndexError, TypeError, VoiceError):
            raise QwenProbeError("invalid_model_json") from None
        result = {
            "requested_model": model,
            "reported_model": reported_model,
            "case": case_id,
            "status": "passed",
            "elapsed_ms": elapsed,
            "usage": tokens,
            "reply_digest": hashlib.sha256(reply.encode()).hexdigest(),
            "reply_characters": len(reply),
            "end": end,
            "tested_at": datetime.now(UTC).isoformat(),
        }
        self.results.append(result)
        return result

    def run(self, models, cases):
        if (
            not models
            or len(models) > 2
            or len(set(models)) != len(models)
            or any(model not in MODELS for model in models)
            or not cases
            or len(cases) > 4
            or len(set(cases)) != len(cases)
            or any(case not in CASES for case in cases)
        ):
            raise QwenProbeError("invalid_probe_selection")
        for model in models:
            for case_id in cases:
                self.call(model, case_id)
        return self.report(True)

    def report(self, passed, error=None):
        return {
            "mode": "qwen_token_plan_llm_probe",
            "credential_state": "configured",
            "credential_source": self.credential_source,
            "llm_verified": passed,
            "endpoint": CHAT,
            "results": self.results,
            "external_calls": self.external_calls,
            "request_state": self.request_state,
            "error": error,
            "active_model": self.active_model,
            "active_case": self.active_case,
            "timing_scope": "nonstream_http_complete_response",
            "first_token_ms": None,
            "retry_performed": False,
            **BOUNDARY,
        }


def main():
    parser = argparse.ArgumentParser(description="Token Plan 固定合成语句 LLM 测试；使用套餐专属端点，可能消耗额度。")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="只检查配置，不请求模型或读取余额")
    probe_parser = sub.add_parser("probe", help="一次 Flash 调用；可显式选择对照或四条测试")
    probe_parser.add_argument("--model", choices=MODELS, default=MODELS[0])
    probe_parser.add_argument("--compare", action="store_true")
    probe_parser.add_argument("--suite", action="store_true")
    probe_parser.add_argument("--acknowledged", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    probe = None
    key = os.getenv(KEY_ENV, "")
    source = "environment" if key else "none"
    try:
        if not args.acknowledged:
            raise QwenProbeError("probe_acknowledgement_required")
        if os.getenv("APP_ENV") == "production":
            raise QwenProbeError("production_probe_disabled")
        if not key:
            if not sys.stdin.isatty():
                raise QwenProbeError("credential_missing_use_local_prompt")
            source = "local_prompt"
            key = getpass.getpass("Token Plan 完整 API Key（不是 Base URL；不回显、不保存）：")
        probe = QwenTokenProbe(key)
        probe.credential_source = source
        models = (args.model, *(model for model in MODELS if model != args.model)) if args.compare else (args.model,)
        cases = tuple(CASES) if args.suite else ("confirm",)
        result = probe.run(models, cases)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        error = (
            str(exc)
            if isinstance(exc, QwenProbeError)
            else "probe_cancelled"
            if isinstance(exc, KeyboardInterrupt)
            else "qwen_probe_failed"
        )
        result = (
            probe.report(False, error)
            if probe
            else {
                **status(key, source),
                "error": error,
                "results": [],
                "request_state": "not_sent",
                "retry_performed": False,
            }
        )
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
