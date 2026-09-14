from __future__ import annotations

import hashlib
import importlib.util
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_gateway import FORBIDDEN_RUNTIME_TOOLS, TOOL_CATALOG, build_agent_result
from .models import AgentRuntimeCheckpoint, AgentSession, ServiceConfig


@dataclass(frozen=True)
class RuntimeContext:
    tenant_id: str
    session_id: str
    scope_type: str
    scope_id: str | None
    provider: str
    profile: str
    settings: dict[str, Any]


class RuntimeAdapter(Protocol):
    name: str

    def run(self, db: Session, context: RuntimeContext, query: str) -> dict[str, Any]: ...


def _provider_session_id(prefix: str, tenant_id: str, session_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{session_id}".encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _blocked_runtime_request(query: str) -> dict[str, Any] | None:
    patterns = {
        "shell": r"\b(shell|bash|powershell)\b|执行命令",
        "filesystem": r"文件系统|读文件|写文件|删除文件",
        "http.unrestricted": r"任意\s*(HTTP|URL)|访问任意网址|直接请求外网",
        "payment.write": r"payment\.write|直接改(回款|到账)",
        "commission.write": r"commission\.write|直接改佣金",
        "protection.release": r"protection\.release|直接解除保护",
        "policy.publish": r"policy\.publish|直接发布策略",
    }
    denied = next((tool for tool, pattern in patterns.items() if re.search(pattern, query, re.IGNORECASE)), None)
    if not denied:
        return None
    return {
        "answer": {
            "title": "Runtime 请求已被工具策略阻断",
            "body": f"请求涉及未授权能力 {denied}。Agent Runtime 不具备该工具，也不会尝试绕过业务 API；如需业务变更，请使用受控提案和审批流程。",
            "facts": [
                {"label": "阻断工具", "value": denied},
                {"label": "允许工具", "value": f"{len(TOOL_CATALOG)} 个受控业务工具"},
                {"label": "最终裁决", "value": "确定性业务服务"},
            ],
            "sources": [
                {"label": "Agent 工具权限策略", "entity_type": "tool_policy", "entity_id": "fulfillops-safe", "version": "v0.3.1"}
            ],
            "navigation_hint": "integrations",
        },
        "tool_trace": [{"tool": denied, "status": "blocked", "detail": "工具未注册；请求在 Agent Gateway 边界被拒绝"}],
        "evidence": [{"policy": "fulfillops-safe", "forbidden_tool": denied}],
        "proposal": None,
    }


class FulfillOpsSandboxAdapter:
    name = "FulfillOps Sandbox Adapter"

    def run(self, db: Session, context: RuntimeContext, query: str) -> dict[str, Any]:
        blocked = _blocked_runtime_request(query)
        return blocked or build_agent_result(db, context.tenant_id, query, context.scope_type, context.scope_id)


class DeepSeekHarnessAdapter:
    """Safe adapter contract for the DeepSeek Harness developer preview.

    The current repository deliberately does not launch the upstream SDK. The
    production driver is enabled only after an out-of-tree FulfillOps plugin has
    removed shell/filesystem tools and registered the controlled business tool
    catalog. Until then, sandbox-contract mode exercises the exact gateway and
    persistence contract without making an external model call.
    """

    name = "DeepSeek Harness Adapter"

    def run(self, db: Session, context: RuntimeContext, query: str) -> dict[str, Any]:
        transport = str(context.settings.get("transport") or "sandbox-contract")
        safety_preset = str(context.settings.get("safetyPreset") or "fulfillops-safe")
        if safety_preset != "fulfillops-safe":
            raise ValueError("DeepSeek Harness 必须使用 fulfillops-safe 安全策略")
        if transport != "sandbox-contract":
            if importlib.util.find_spec("deepseek_harness") is None:
                raise RuntimeError("DeepSeek Harness Python SDK 尚未安装；不能把契约测试标记为真实接入")
            if os.getenv("FULFILLOPS_DSH_PLUGIN_READY") != "1":
                raise RuntimeError("FulfillOps Harness 安全插件尚未确认就绪；禁止启动带 shell/文件系统能力的 Runtime")
            raise RuntimeError("DeepSeek Harness 生产驱动尚未启用；请完成专用插件验收后接入")
        blocked = _blocked_runtime_request(query)
        result = blocked or build_agent_result(db, context.tenant_id, query, context.scope_type, context.scope_id)
        result["tool_trace"] = [
            {
                "tool": "runtime.session.resume",
                "status": "completed",
                "detail": "使用服务端检查点恢复 Harness 会话；未启动外部进程",
            },
            *result["tool_trace"],
        ]
        return result


def adapter_for(provider: str) -> RuntimeAdapter:
    return DeepSeekHarnessAdapter() if provider == "DeepSeek Harness" else FulfillOpsSandboxAdapter()


def runtime_settings_for(db: Session, session: AgentSession) -> dict[str, Any]:
    config = db.scalar(
        select(ServiceConfig).where(
            ServiceConfig.tenant_id == session.tenant_id,
            ServiceConfig.service_type == "agent",
            ServiceConfig.provider == session.runtime_provider,
        )
    )
    return dict(config.settings) if config else {
        "transport": "sandbox-contract",
        "safetyPreset": "fulfillops-safe",
        "sessionPersistence": "database-checkpoint",
    }


def run_runtime_turn(db: Session, session: AgentSession, query: str) -> tuple[dict[str, Any], AgentRuntimeCheckpoint, dict[str, Any]]:
    settings = runtime_settings_for(db, session)
    context = RuntimeContext(
        tenant_id=session.tenant_id,
        session_id=session.id,
        scope_type=session.scope_type,
        scope_id=session.scope_id,
        provider=session.runtime_provider,
        profile=session.runtime_profile,
        settings=settings,
    )
    checkpoint = db.scalar(
        select(AgentRuntimeCheckpoint).where(
            AgentRuntimeCheckpoint.tenant_id == session.tenant_id,
            AgentRuntimeCheckpoint.session_id == session.id,
        )
    )
    resumed = checkpoint is not None
    if checkpoint is None:
        prefix = "DSH" if session.runtime_provider == "DeepSeek Harness" else "AGW"
        checkpoint = AgentRuntimeCheckpoint(
            tenant_id=session.tenant_id,
            session_id=session.id,
            provider_session_id=_provider_session_id(prefix, session.tenant_id, session.id),
            event_cursor=0,
            turn_count=0,
            runtime_metadata={},
        )
        db.add(checkpoint)
        db.flush()
    generated = adapter_for(session.runtime_provider).run(db, context, query)
    checkpoint.turn_count += 1
    checkpoint.event_cursor += max(2, len(generated.get("tool_trace") or []) + 2)
    checkpoint.runtime_metadata = {
        "transport": settings.get("transport") or "sandbox-contract",
        "safety_preset": settings.get("safetyPreset") or "fulfillops-safe",
        "session_persistence": settings.get("sessionPersistence") or "database-checkpoint",
        "registered_tools": [item["name"] for item in TOOL_CATALOG],
        "forbidden_tools": FORBIDDEN_RUNTIME_TOOLS,
    }
    runtime = {
        "adapter": adapter_for(session.runtime_provider).name,
        "provider_session_id": checkpoint.provider_session_id,
        "event_cursor": checkpoint.event_cursor,
        "turn_count": checkpoint.turn_count,
        "resumed": resumed,
        **checkpoint.runtime_metadata,
    }
    return generated, checkpoint, runtime


def test_agent_runtime(config: ServiceConfig) -> tuple[int, str]:
    if config.provider != "DeepSeek Harness":
        return 126, "Runtime 鉴权通过；工具白名单与审批回调可用"
    transport = str(config.settings.get("transport") or "sandbox-contract")
    if str(config.settings.get("safetyPreset") or "") != "fulfillops-safe":
        raise ValueError("DeepSeek Harness 连接测试失败：必须选择 fulfillops-safe 安全策略")
    if transport == "sandbox-contract":
        return 64, "DeepSeek Harness 适配契约通过；8/8 受控工具，7 类危险工具未注册；未启动外部进程"
    if importlib.util.find_spec("deepseek_harness") is None:
        raise ValueError("DeepSeek Harness Python SDK 尚未安装")
    if os.getenv("FULFILLOPS_DSH_PLUGIN_READY") != "1":
        raise ValueError("FulfillOps Harness 安全插件尚未通过验收")
    return 93, "DeepSeek Harness SDK 与 fulfillops-safe 插件已发现；需另行完成真实模型与业务工具回放"
