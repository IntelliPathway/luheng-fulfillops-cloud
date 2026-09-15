from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from .models import ServiceConfig
from .secret_store import SecretStoreError, resolve_secret

CONTRACT_MODE = "contract-only"
LIVE_MODE = "live-provider"
SUPPORTED_EXECUTION_MODES = {CONTRACT_MODE, LIVE_MODE}
DEFAULT_EGRESS_HOSTS = {"api.deepseek.com"}
RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 1_000_000


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable


@dataclass(frozen=True)
class ModelPolicy:
    execution_mode: str
    provider: str
    model: str
    endpoint_url: str
    endpoint_host: str
    timeout_seconds: int
    max_output_tokens: int
    max_cost_usd: float
    cost_ceiling_per_million_tokens: float
    egress_allowed: bool


@dataclass(frozen=True)
class ModelInvocation:
    content: dict[str, Any]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    request_digest: str
    response_digest: str
    provider_request_id_hash: str | None


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def live_model_calls_enabled() -> bool:
    return _truthy(os.getenv("ENABLE_LIVE_MODEL_CALLS"), False)


def _setting_int(settings: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ModelGatewayError("invalid_policy", f"{key} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ModelGatewayError("invalid_policy", f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _setting_float(settings: dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ModelGatewayError("invalid_policy", f"{key} 必须是数字") from exc
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ModelGatewayError("invalid_policy", f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _allowed_hosts() -> set[str]:
    configured = os.getenv("MODEL_EGRESS_ALLOWLIST", "").strip()
    hosts = {item.strip().lower().rstrip(".") for item in configured.split(",") if item.strip()}
    return hosts or DEFAULT_EGRESS_HOSTS


def _is_private_literal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)


def _endpoint_url(raw: str) -> tuple[str, str, bool]:
    parsed = urlparse(raw.strip())
    allow_insecure = _truthy(os.getenv("MODEL_ALLOW_INSECURE_HTTP"), False)
    if parsed.scheme not in ({"https", "http"} if allow_insecure else {"https"}):
        raise ModelGatewayError("egress_denied", "模型 Endpoint 必须使用 HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelGatewayError("egress_denied", "模型 Endpoint 格式无效或包含不允许的凭证、查询参数")
    host = parsed.hostname.lower().rstrip(".")
    if (
        host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or _is_private_literal(host)
    ) and not _truthy(os.getenv("MODEL_ALLOW_PRIVATE_EGRESS"), False):
        raise ModelGatewayError("egress_denied", "模型 Endpoint 不允许访问本地或私有地址")
    if parsed.port and parsed.port not in ({443, 80} if allow_insecure else {443}):
        raise ModelGatewayError("egress_denied", "模型 Endpoint 端口不在允许范围")
    allowed = host in _allowed_hosts()
    if not allowed:
        raise ModelGatewayError("egress_denied", "模型 Endpoint 未命中部署出网白名单")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        target_path = path
    elif path.endswith("/v1"):
        target_path = f"{path}/chat/completions"
    elif path in {"", "/"}:
        target_path = "/chat/completions"
    else:
        target_path = f"{path}/chat/completions"
    endpoint = urlunparse((parsed.scheme, parsed.netloc, target_path, "", "", ""))
    return endpoint, host, allowed


def model_policy_settings(provider: str, settings_input: dict[str, Any]) -> ModelPolicy:
    settings = dict(settings_input or {})
    mode = str(settings.get("executionMode") or CONTRACT_MODE)
    if mode not in SUPPORTED_EXECUTION_MODES:
        raise ModelGatewayError("invalid_policy", "模型执行模式不受支持")
    endpoint, host, allowed = _endpoint_url(str(settings.get("endpoint") or ""))
    model = str(settings.get("model") or "").strip()
    if not model or len(model) > 120:
        raise ModelGatewayError("invalid_policy", "模型名称无效")
    if mode == LIVE_MODE:
        configured_models = os.getenv("MODEL_ALLOWED_MODELS", "deepseek-flash,deepseek-v4-pro")
        allowed_models = {item.strip() for item in configured_models.split(",") if item.strip()}
        if model not in allowed_models:
            raise ModelGatewayError("model_denied", "模型名称未命中部署允许列表")
    timeout_seconds = _setting_int(settings, "timeout", 30, 5, 60)
    max_output_tokens = _setting_int(settings, "maxOutputTokens", 512, 64, 4096)
    try:
        deployment_budget = float(os.getenv("MODEL_MAX_COST_USD_PER_CALL", "0.25"))
    except ValueError as exc:
        raise ModelGatewayError("invalid_policy", "部署级模型费用上限配置无效") from exc
    if not 0.001 <= deployment_budget <= 5 or not math.isfinite(deployment_budget):
        raise ModelGatewayError("invalid_policy", "部署级模型费用上限必须在 0.001 到 5 美元之间")
    max_cost_usd = _setting_float(settings, "maxCostUsd", 0.05, 0.001, min(5.0, deployment_budget))
    try:
        cost_ceiling = float(os.getenv("MODEL_COST_CEILING_USD_PER_M_TOKENS", "20"))
    except ValueError as exc:
        raise ModelGatewayError("invalid_policy", "模型费用安全上界配置无效") from exc
    if not 0.01 <= cost_ceiling <= 1000 or not math.isfinite(cost_ceiling):
        raise ModelGatewayError("invalid_policy", "模型费用安全上界配置无效")
    return ModelPolicy(
        execution_mode=mode,
        provider=provider,
        model=model,
        endpoint_url=endpoint,
        endpoint_host=host,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        max_cost_usd=max_cost_usd,
        cost_ceiling_per_million_tokens=cost_ceiling,
        egress_allowed=allowed,
    )


def model_policy(config: ServiceConfig) -> ModelPolicy:
    return model_policy_settings(config.provider, dict(config.settings or {}))


def policy_snapshot(config: ServiceConfig) -> dict[str, Any]:
    policy = model_policy(config)
    return {
        "config_version": config.version,
        "execution_mode": policy.execution_mode,
        "provider": policy.provider,
        "model": policy.model,
        "endpoint_host": policy.endpoint_host,
        "timeout_seconds": policy.timeout_seconds,
        "max_output_tokens": policy.max_output_tokens,
        "max_cost_usd": policy.max_cost_usd,
        "cost_ceiling_per_million_tokens": policy.cost_ceiling_per_million_tokens,
        "egress_allowed": policy.egress_allowed,
        "redirects_allowed": False,
        "data_policy": "synthetic-or-redacted-only",
    }


def model_gateway_health(config: ServiceConfig | None) -> dict[str, Any]:
    if not config:
        return {
            "status": "unconfigured",
            "configured": False,
            "provider": None,
            "model": None,
            "config_version": None,
            "execution_mode": CONTRACT_MODE,
            "endpoint_host": None,
            "egress_allowed": False,
            "credential_configured": False,
            "live_calls_enabled": live_model_calls_enabled(),
            "connected": False,
            "max_output_tokens": 0,
            "max_cost_usd": 0.0,
            "detail": "尚未配置模型服务",
        }
    try:
        policy = model_policy(config)
    except ModelGatewayError as exc:
        raw_mode = str(config.settings.get("executionMode") or CONTRACT_MODE)
        return {
            "status": "degraded",
            "configured": True,
            "provider": config.provider,
            "model": str(config.settings.get("model") or "") or None,
            "config_version": config.version,
            "execution_mode": raw_mode if raw_mode in SUPPORTED_EXECUTION_MODES else CONTRACT_MODE,
            "endpoint_host": None,
            "egress_allowed": False,
            "credential_configured": bool(config.secret_ref),
            "live_calls_enabled": live_model_calls_enabled(),
            "connected": config.connected,
            "max_output_tokens": 0,
            "max_cost_usd": 0.0,
            "detail": str(exc),
        }
    live_ready = (
        policy.execution_mode == LIVE_MODE
        and live_model_calls_enabled()
        and policy.egress_allowed
        and bool(config.secret_ref)
        and config.connected
    )
    return {
        "status": "ready" if live_ready else "contract",
        "configured": True,
        "provider": policy.provider,
        "model": policy.model,
        "config_version": config.version,
        "execution_mode": policy.execution_mode,
        "endpoint_host": policy.endpoint_host,
        "egress_allowed": policy.egress_allowed,
        "credential_configured": bool(config.secret_ref),
        "live_calls_enabled": live_model_calls_enabled(),
        "connected": config.connected,
        "max_output_tokens": policy.max_output_tokens,
        "max_cost_usd": policy.max_cost_usd,
        "detail": "真实模型调用门禁已就绪" if live_ready else "当前仅验证模型契约，不会发起外部请求",
    }


def _request_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _provider_error(status_code: int) -> ModelGatewayError:
    code = {
        400: "invalid_request",
        401: "authentication_failed",
        402: "insufficient_balance",
        403: "provider_forbidden",
        422: "invalid_parameters",
        429: "rate_limited",
        500: "provider_error",
        502: "provider_unavailable",
        503: "provider_overloaded",
        504: "provider_timeout",
    }.get(status_code, "provider_rejected")
    return ModelGatewayError(code, f"模型 Provider 请求失败（{code}）", retriable=status_code in RETRIABLE_STATUS_CODES)


def invoke_json_model(
    config: ServiceConfig,
    credential: str,
    tenant_id: str,
    system_prompt: str,
    user_prompt: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ModelInvocation:
    policy = model_policy(config)
    if policy.execution_mode != LIVE_MODE:
        raise ModelGatewayError("live_mode_required", "模型配置尚未启用真实 Provider 模式")
    if not live_model_calls_enabled():
        raise ModelGatewayError("live_calls_disabled", "部署环境未启用真实模型调用")
    if not credential:
        raise ModelGatewayError("credential_missing", "模型凭证不可用")
    if len(system_prompt) + len(user_prompt) > 12_000:
        raise ModelGatewayError("input_too_large", "模型输入超过受控调用上限")
    estimated_input_tokens = math.ceil((len(system_prompt) + len(user_prompt)) / 2)
    reserved_cost = (
        (estimated_input_tokens + policy.max_output_tokens) * policy.cost_ceiling_per_million_tokens / 1_000_000
    )
    if reserved_cost > policy.max_cost_usd:
        raise ModelGatewayError("budget_exceeded", "模型调用的保守费用预估超过单次预算")
    payload = {
        "model": policy.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": policy.max_output_tokens,
        "stream": False,
        "user_id": f"tenant-{hashlib.sha256(tenant_id.encode()).hexdigest()[:16]}",
    }
    request_digest = _request_digest(payload)
    started = time.monotonic()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(policy.timeout_seconds),
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.post(
                policy.endpoint_url,
                headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise ModelGatewayError("provider_timeout", "模型 Provider 请求超时", retriable=True) from exc
    except httpx.HTTPError as exc:
        raise ModelGatewayError("provider_unavailable", "模型 Provider 网络不可用", retriable=True) from exc
    latency_ms = max(1, round((time.monotonic() - started) * 1000))
    if 300 <= response.status_code < 400:
        raise ModelGatewayError("redirect_denied", "模型 Provider 返回了不允许的重定向")
    if response.status_code != 200:
        raise _provider_error(response.status_code)
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ModelGatewayError("response_too_large", "模型 Provider 响应超过大小上限")
    try:
        body = response.json()
        content_raw = body["choices"][0]["message"]["content"]
        if not isinstance(content_raw, str) or not content_raw.strip():
            raise ValueError("empty content")
        content = json.loads(content_raw)
        if not isinstance(content, dict):
            raise TypeError("JSON output is not an object")
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ModelGatewayError("invalid_response", "模型 Provider 未返回有效的 JSON Object") from exc
    estimated_cost = (input_tokens + output_tokens) * policy.cost_ceiling_per_million_tokens / 1_000_000
    if estimated_cost > policy.max_cost_usd:
        raise ModelGatewayError("budget_exceeded_after_call", "模型 Provider 实际用量超过单次预算")
    response_digest = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    provider_request_id = response.headers.get("x-request-id") or response.headers.get("x-deepseek-request-id")
    provider_request_id_hash = (
        hashlib.sha256(provider_request_id.encode()).hexdigest()[:16] if provider_request_id else None
    )
    return ModelInvocation(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=round(estimated_cost, 8),
        latency_ms=latency_ms,
        request_digest=request_digest,
        response_digest=response_digest,
        provider_request_id_hash=provider_request_id_hash,
    )


def test_model_runtime(db: Session, config: ServiceConfig) -> tuple[int, str]:
    policy = model_policy(config)
    if policy.execution_mode == CONTRACT_MODE:
        return 72, "模型安全契约通过；出网白名单、JSON Object、超时与单次费用门禁有效；未发起外部请求"
    if not live_model_calls_enabled():
        raise ModelGatewayError("live_calls_disabled", "部署环境未启用真实模型调用")
    try:
        credential = resolve_secret(db, config.secret_ref, config.tenant_id, "model")
    except SecretStoreError as exc:
        raise ModelGatewayError("credential_unavailable", "模型凭证无法从密钥后端解析") from exc
    result = invoke_json_model(
        config,
        credential or "",
        config.tenant_id,
        'Return only a JSON object matching this example: {"status":"ok"}.',
        "Connectivity probe. Do not use tools. Return JSON now.",
    )
    if result.content.get("status") != "ok":
        raise ModelGatewayError("probe_failed", "模型 Provider JSON 探针未返回预期状态")
    return result.latency_ms, "真实模型 JSON 探针通过；未发送案件、通话或账务数据"
