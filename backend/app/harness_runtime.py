from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .security import issue_runtime_token

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是履衡 AI 的任务规划与解释 Runtime。你只能使用 mcp__fulfillops 命名空间内的受控工具。
回答必须以工具返回的租户事实为依据；不要猜测案件、金额、策略或运行状态。暂停与恢复只能调用 propose 工具生成提案，
不得声称已经执行。禁止尝试 shell、文件系统、任意网络请求、账务写入、解除保护或策略发布。请用简洁中文回答。"""

TRACE_NAME_MAP = {
    "mcp__fulfillops__case_read": "case.read",
    "mcp__fulfillops__activity_read": "activity.read",
    "mcp__fulfillops__metrics_query": "metrics.query",
    "mcp__fulfillops__policy_read": "policy.read",
    "mcp__fulfillops__knowledge_search": "knowledge.search",
    "mcp__fulfillops__run_read": "run.read",
    "mcp__fulfillops__activity_pause_propose": "activity.pause.propose",
    "mcp__fulfillops__activity_resume_propose": "activity.resume.propose",
}


@dataclass(frozen=True)
class HarnessLaunchContext:
    tenant_id: str
    session_id: str
    scope_type: str
    scope_id: str | None
    settings: dict[str, Any]
    logical_provider_session_id: str
    prior_turn_count: int
    previous_metadata: dict[str, Any]
    replay_messages: list[dict[str, str]]


@dataclass
class _RuntimeEntry:
    client: Any
    lock: threading.Lock
    provider_session_id: str
    process_generation: int
    token_expires_at: datetime
    config_fingerprint: str


def _truthy(value: str | None) -> bool:
    return bool(value and value.lower() in {"1", "true", "yes", "on"})


def _sdk_class() -> type:
    try:
        module = importlib.import_module("deepseek_harness")
    except ImportError as exc:
        raise RuntimeError("DeepSeek Harness Python SDK 尚未安装；请安装 requirements-harness.txt") from exc
    sdk = getattr(module, "DeepSeekHarness", None)
    if sdk is None:
        raise RuntimeError("DeepSeek Harness SDK 缺少 DeepSeekHarness 入口")
    return sdk


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _patch_path() -> Path:
    return _backend_root() / "harness" / "fulfillops-safe.patch.yml"


def _hashed_scope(tenant_id: str, session_id: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{session_id}".encode()).hexdigest()[:24]


def _runtime_paths(context: HarnessLaunchContext) -> tuple[Path, Path]:
    root = Path(os.getenv("FULFILLOPS_DSH_ROOT", "/tmp/luheng-fulfillops-dsh")).resolve()
    scope = _hashed_scope(context.tenant_id, context.session_id)
    dsh_home = root / "homes" / scope
    workspace = root / "workspaces" / scope
    dsh_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    return dsh_home, workspace


def _model_settings(settings: dict[str, Any]) -> tuple[str, str, str | None]:
    model_service = settings.get("modelService") if isinstance(settings.get("modelService"), dict) else {}
    provider_name = str(model_service.get("provider") or "DeepSeek")
    if provider_name != "DeepSeek":
        raise RuntimeError("当前 Harness 安全 Profile 仅验收 DeepSeek 模型路由；其他模型需单独适配器")
    provider = str(settings.get("providerRoute") or "deepseek-official")
    model = str(model_service.get("model") or os.getenv("DSH_MODEL") or "deepseek-chat")
    base_url = str(model_service.get("endpoint") or os.getenv("DEEPSEEK_BASE_URL") or "").strip() or None
    return provider, model, base_url


def _launch_kwargs(context: HarnessLaunchContext, token: str) -> dict[str, Any]:
    patch = _patch_path()
    if not patch.is_file():
        raise RuntimeError("fulfillops-safe Harness patch 不存在")
    dsh_home, workspace = _runtime_paths(context)
    provider, model, base_url = _model_settings(context.settings)
    api_key = str(context.settings.get("_modelCredential") or os.getenv("DEEPSEEK_API_KEY") or "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未注入 Runtime 进程；KMS 引用不能自动当作明文凭证")
    backend_root = _backend_root()
    return {
        "dsh_home": str(dsh_home),
        "cwd": str(workspace),
        "runtime_cwd": str(workspace),
        "provider": provider,
        "model": model,
        "profile": "sdk-minimal",
        "patches": (str(patch),),
        "initialize_timeout_seconds": float(context.settings.get("initializeTimeoutSeconds") or 30),
        "request_timeout_seconds": float(context.settings.get("requestTimeoutSeconds") or 120),
        "shutdown_timeout_seconds": 3.0,
        "base_url": base_url,
        "api_key": api_key,
        "env": {
            "DSH_SYSTEM_PROMPT": SYSTEM_PROMPT,
            "FULFILLOPS_MCP_PYTHON": sys.executable,
            "FULFILLOPS_MCP_CWD": str(backend_root),
            "FULFILLOPS_MCP_PYTHONPATH": str(backend_root),
            "FULFILLOPS_MCP_BASE_URL": os.getenv("FULFILLOPS_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/"),
            "FULFILLOPS_MCP_TOKEN": token,
        },
    }


def validate_runtime_prerequisites(settings: dict[str, Any]) -> None:
    if not _truthy(os.getenv("FULFILLOPS_ENABLE_DSH_RUNTIME")):
        raise RuntimeError("真实 Harness Runtime 未显式启用；请设置 FULFILLOPS_ENABLE_DSH_RUNTIME=1")
    _sdk_class()
    if not _patch_path().is_file():
        raise RuntimeError("fulfillops-safe Harness patch 不存在")
    if not settings.get("_modelCredential") and not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY 未通过运行环境或 KMS 注入")
    _model_settings(settings)


def _fingerprint(settings: dict[str, Any]) -> str:
    selected = {
        key: settings.get(key)
        for key in (
            "transport",
            "safetyPreset",
            "providerRoute",
            "initializeTimeoutSeconds",
            "requestTimeoutSeconds",
            "modelService",
        )
    }
    return hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _replay_prompt(messages: list[dict[str, str]], query: str) -> str:
    if not messages:
        return query
    transcript = "\n".join(
        f"{('用户' if item.get('role') == 'user' else '履衡 AI')}: {str(item.get('content') or '')[:1200]}"
        for item in messages[-12:]
    )
    return (
        "以下是履衡数据库中保存的脱敏会话检查点。它只用于恢复上下文，不代表工具事实仍然有效；"
        "涉及案件、金额、活动或策略时必须重新调用受控工具。\n\n"
        f"[历史检查点]\n{transcript}\n\n[当前请求]\n{query}"
    )


def _json_from_tool_result(event: dict[str, Any]) -> dict[str, Any] | None:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    for wrapper in message.get("content") if isinstance(message.get("content"), list) else []:
        if not isinstance(wrapper, dict) or wrapper.get("type") != "tool-result" or wrapper.get("isError"):
            continue
        for block in wrapper.get("content") if isinstance(wrapper.get("content"), list) else []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            try:
                value = json.loads(str(block.get("text") or ""))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None


def project_run_result(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    events = list(getattr(result, "events", []) or [])
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    verified_results: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "tool/call":
            call_id = str(data.get("callId") or f"call-{len(order) + 1}")
            raw_name = str(data.get("name") or "unknown")
            name = TRACE_NAME_MAP.get(raw_name, raw_name)
            calls[call_id] = {
                "tool": name,
                "status": "running",
                "detail": "DeepSeek Harness 已调用受控 MCP 工具",
            }
            order.append(call_id)
        elif event_type == "tool/result":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            source = message.get("source") if isinstance(message.get("source"), dict) else {}
            call_id = str(source.get("callId") or "")
            wrappers = message.get("content") if isinstance(message.get("content"), list) else []
            is_error = any(
                isinstance(item, dict) and item.get("type") == "tool-result" and item.get("isError")
                for item in wrappers
            )
            if call_id in calls:
                calls[call_id]["status"] = "failed" if is_error else "completed"
                calls[call_id]["detail"] = (
                    "MCP 工具返回错误" if is_error else "工具结果已由 FulfillOps API 在租户范围内生成"
                )
            structured = _json_from_tool_result(event)
            if structured is not None:
                verified_results.append(structured)

    tool_trace = [calls[call_id] for call_id in order]
    candidate = next((item for item in reversed(verified_results) if isinstance(item.get("answer"), dict)), None)
    final_response = str(getattr(result, "final_response", "") or "").strip()
    if candidate:
        answer = dict(candidate["answer"])
        if final_response:
            answer["body"] = final_response
        evidence = [
            entry for item in verified_results for entry in (item.get("evidence") or []) if isinstance(entry, dict)
        ]
        proposal = next((item.get("proposal") for item in reversed(verified_results) if item.get("proposal")), None)
    else:
        answer = {
            "title": "Harness Runtime 回答",
            "body": final_response or "Runtime 未产生可交付回答。",
            "facts": [{"label": "已核验业务工具", "value": "0 个"}],
            "sources": [],
            "navigation_hint": "agents",
        }
        evidence = [{"verified_tool_results": 0}]
        proposal = None
        tool_trace.append(
            {
                "tool": "runtime.answer",
                "status": "unverified",
                "detail": "本轮未取得可解析的受控工具结果；回答不作为业务事实或行动依据",
            }
        )
    generated = {
        "answer": answer,
        "tool_trace": tool_trace,
        "evidence": evidence,
        "proposal": proposal,
    }
    metadata = {
        "sdk_event_count": len(events),
        "verified_tool_count": len(verified_results),
        "finish_reason": getattr(result, "finish_reason", None),
    }
    return generated, metadata


class HarnessRuntimeManager:
    def __init__(self, sdk_loader: Callable[[], type] = _sdk_class) -> None:
        self._sdk_loader = sdk_loader
        self._entries: dict[str, _RuntimeEntry] = {}
        self._lock = threading.Lock()

    def _key(self, context: HarnessLaunchContext) -> str:
        return f"{context.tenant_id}:{context.session_id}"

    def _close_entry(self, entry: _RuntimeEntry) -> None:
        try:
            entry.client.close()
        # Shutdown is best-effort, but retaining the failure in application logs
        # makes orphaned Runtime processes diagnosable.
        except Exception:
            logger.exception("DeepSeek Harness Runtime 关闭失败")

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            self._close_entry(entry)

    def evict(self, context: HarnessLaunchContext) -> None:
        with self._lock:
            entry = self._entries.pop(self._key(context), None)
        if entry:
            self._close_entry(entry)

    def _new_entry(self, context: HarnessLaunchContext, fingerprint: str) -> _RuntimeEntry:
        token, expires_at = issue_runtime_token(
            context.tenant_id,
            context.session_id,
            context.scope_type,
            context.scope_id,
        )
        previous_generation = int(context.previous_metadata.get("process_generation") or 0)
        generation = previous_generation + 1
        provider_session_id = context.logical_provider_session_id
        if context.prior_turn_count:
            provider_session_id = f"{provider_session_id}-r{generation}"
        client = self._sdk_loader()(**_launch_kwargs(context, token))
        return _RuntimeEntry(
            client=client,
            lock=threading.Lock(),
            provider_session_id=provider_session_id,
            process_generation=generation,
            token_expires_at=expires_at,
            config_fingerprint=fingerprint,
        )

    def run(self, context: HarnessLaunchContext, query: str) -> tuple[dict[str, Any], dict[str, Any]]:
        validate_runtime_prerequisites(context.settings)
        key = self._key(context)
        fingerprint = _fingerprint(context.settings)
        with self._lock:
            entry = self._entries.get(key)
            stale = bool(
                entry
                and (
                    entry.config_fingerprint != fingerprint
                    or entry.token_expires_at <= datetime.now(UTC) + timedelta(minutes=2)
                )
            )
            if stale:
                self._entries.pop(key, None)
            if not entry or stale:
                old_entry = entry if stale else None
                entry = self._new_entry(context, fingerprint)
                self._entries[key] = entry
            else:
                old_entry = None
        if old_entry:
            self._close_entry(old_entry)

        in_process_resume = context.prior_turn_count > 0 and entry.process_generation == int(
            context.previous_metadata.get("process_generation") or 0
        )
        replayed = context.prior_turn_count > 0 and not in_process_resume
        prompt = _replay_prompt(context.replay_messages, query) if replayed else query
        try:
            with entry.lock:
                result = entry.client.run(prompt, session_id=entry.provider_session_id)
        except Exception:
            self.evict(context)
            raise
        generated, sdk_metadata = project_run_result(result)
        runtime = {
            "provider_session_id": entry.provider_session_id,
            "process_generation": entry.process_generation,
            "resume_mode": "checkpoint-replay" if replayed else "in-process" if in_process_resume else "new",
            "replayed_message_count": len(context.replay_messages) if replayed else 0,
            "sdk_protocol": "json-rpc/stdio",
            **sdk_metadata,
        }
        return generated, runtime

    def probe(self, tenant_id: str, settings: dict[str, Any]) -> None:
        validate_runtime_prerequisites(settings)
        context = HarnessLaunchContext(
            tenant_id=tenant_id,
            session_id="connection-test",
            scope_type="global",
            scope_id=None,
            settings=settings,
            logical_provider_session_id="DSH-CONNECTION-TEST",
            prior_turn_count=0,
            previous_metadata={},
            replay_messages=[],
        )
        token, _ = issue_runtime_token(tenant_id, context.session_id, "global", None, expires_minutes=5)
        client = self._sdk_loader()(**_launch_kwargs(context, token))
        try:
            client.start()
        finally:
            client.close()


HARNESS_RUNTIME_MANAGER = HarnessRuntimeManager()


def close_harness_runtimes() -> None:
    HARNESS_RUNTIME_MANAGER.close_all()
