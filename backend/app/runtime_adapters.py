from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_gateway import FORBIDDEN_RUNTIME_TOOLS, TOOL_CATALOG, build_agent_result
from .harness_runtime import HARNESS_RUNTIME_MANAGER, HarnessLaunchContext
from .models import AgentMessage, AgentRuntimeCheckpoint, AgentSession, ServiceConfig
from .secret_store import resolve_secret


@dataclass(frozen=True)
class RuntimeContext:
    tenant_id: str
    session_id: str
    scope_type: str
    scope_id: str | None
    provider: str
    profile: str
    settings: dict[str, Any]
    logical_provider_session_id: str
    prior_turn_count: int
    previous_metadata: dict[str, Any]
    replay_messages: list[dict[str, str]]


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
                {
                    "label": "Agent 工具权限策略",
                    "entity_type": "tool_policy",
                    "entity_id": "fulfillops-safe",
                    "version": "v0.4",
                }
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

    sandbox-contract mode keeps a zero-provider deterministic path. python-sdk
    mode launches the official subprocess SDK with the fulfillops-safe overlay:
    the shipped persistent shell is disabled and only the scoped MCP tool
    bridge is registered.
    """

    name = "DeepSeek Harness Adapter"

    def run(self, db: Session, context: RuntimeContext, query: str) -> dict[str, Any]:
        transport = str(context.settings.get("transport") or "sandbox-contract")
        safety_preset = str(context.settings.get("safetyPreset") or "fulfillops-safe")
        if safety_preset != "fulfillops-safe":
            raise ValueError("DeepSeek Harness 必须使用 fulfillops-safe 安全策略")
        blocked = _blocked_runtime_request(query)
        if blocked:
            return blocked
        if transport == "python-sdk":
            generated, runtime = HARNESS_RUNTIME_MANAGER.run(
                HarnessLaunchContext(
                    tenant_id=context.tenant_id,
                    session_id=context.session_id,
                    scope_type=context.scope_type,
                    scope_id=context.scope_id,
                    settings=context.settings,
                    logical_provider_session_id=context.logical_provider_session_id,
                    prior_turn_count=context.prior_turn_count,
                    previous_metadata=context.previous_metadata,
                    replay_messages=context.replay_messages,
                ),
                query,
            )
            generated["_runtime"] = runtime
            return generated
        if transport != "sandbox-contract":
            raise RuntimeError(f"不支持的 DeepSeek Harness 传输模式：{transport}")
        result = build_agent_result(db, context.tenant_id, query, context.scope_type, context.scope_id)
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
    settings = (
        dict(config.settings)
        if config
        else {
            "transport": "sandbox-contract",
            "safetyPreset": "fulfillops-safe",
            "sessionPersistence": "database-checkpoint",
        }
    )
    model_config = db.scalar(
        select(ServiceConfig).where(
            ServiceConfig.tenant_id == session.tenant_id,
            ServiceConfig.service_type == "model",
        )
    )
    if model_config:
        settings["modelService"] = {"provider": model_config.provider, **dict(model_config.settings)}
        credential = resolve_secret(db, model_config.secret_ref, session.tenant_id, "model")
        if credential:
            settings["_modelCredential"] = credential
    return settings


def run_runtime_turn(
    db: Session, session: AgentSession, query: str
) -> tuple[dict[str, Any], AgentRuntimeCheckpoint, dict[str, Any]]:
    settings = runtime_settings_for(db, session)
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
    replay_messages = [
        {"role": message.role, "content": message.content}
        for message in db.scalars(
            select(AgentMessage)
            .where(AgentMessage.tenant_id == session.tenant_id, AgentMessage.session_id == session.id)
            .order_by(AgentMessage.created_at.desc())
            .limit(13)
        )
    ][::-1]
    if replay_messages and replay_messages[-1]["role"] == "user" and replay_messages[-1]["content"] == query:
        replay_messages.pop()
    context = RuntimeContext(
        tenant_id=session.tenant_id,
        session_id=session.id,
        scope_type=session.scope_type,
        scope_id=session.scope_id,
        provider=session.runtime_provider,
        profile=session.runtime_profile,
        settings=settings,
        logical_provider_session_id=checkpoint.provider_session_id,
        prior_turn_count=checkpoint.turn_count,
        previous_metadata=dict(checkpoint.runtime_metadata or {}),
        replay_messages=replay_messages,
    )
    generated = adapter_for(session.runtime_provider).run(db, context, query)
    runtime_details = generated.pop("_runtime", {})
    checkpoint.turn_count += 1
    checkpoint.event_cursor += max(
        2,
        int(runtime_details.get("sdk_event_count") or 0),
        len(generated.get("tool_trace") or []) + 2,
    )
    if runtime_details.get("provider_session_id"):
        checkpoint.provider_session_id = str(runtime_details["provider_session_id"])
    checkpoint.runtime_metadata = {
        "transport": settings.get("transport") or "sandbox-contract",
        "safety_preset": settings.get("safetyPreset") or "fulfillops-safe",
        "session_persistence": settings.get("sessionPersistence") or "database-checkpoint",
        "registered_tools": [item["name"] for item in TOOL_CATALOG],
        "forbidden_tools": FORBIDDEN_RUNTIME_TOOLS,
        **runtime_details,
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


def test_agent_runtime(
    config: ServiceConfig,
    model_config: ServiceConfig | None = None,
    db: Session | None = None,
) -> tuple[int, str]:
    if config.provider != "DeepSeek Harness":
        return 126, "Runtime 鉴权通过；工具白名单与审批回调可用"
    transport = str(config.settings.get("transport") or "sandbox-contract")
    if str(config.settings.get("safetyPreset") or "") != "fulfillops-safe":
        raise ValueError("DeepSeek Harness 连接测试失败：必须选择 fulfillops-safe 安全策略")
    if transport == "sandbox-contract":
        return 64, "DeepSeek Harness 适配契约通过；8/8 受控工具，7 类危险工具未注册；未启动外部进程"
    if transport != "python-sdk":
        raise ValueError(f"DeepSeek Harness 连接测试失败：不支持传输模式 {transport}")
    try:
        settings = dict(config.settings)
        if model_config:
            settings["modelService"] = {"provider": model_config.provider, **dict(model_config.settings)}
            if db:
                credential = resolve_secret(db, model_config.secret_ref, config.tenant_id, "model")
                if credential:
                    settings["_modelCredential"] = credential
        HARNESS_RUNTIME_MANAGER.probe(config.tenant_id, settings)
    except RuntimeError as exc:
        raise ValueError(f"DeepSeek Harness 连接测试失败：{exc}") from exc
    return 93, "DeepSeek Harness SDK 握手通过；fulfillops-safe Patch 已禁用 Shell，并发现 8 个受控 MCP 工具"
