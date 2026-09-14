from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_gateway import build_agent_result
from .models import Activity, AgentRun, AssetPackage

ALLOWED_TOOL_NAMES = {
    "case.read",
    "activity.read",
    "metrics.query",
    "policy.read",
    "knowledge.search",
    "run.read",
    "activity.pause.propose",
    "activity.resume.propose",
}


def _required_id(arguments: dict[str, Any], key: str, pattern: str) -> str:
    value = str(arguments.get(key) or "").strip().upper()
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{key} 格式无效")
    return value


def _source(label: str, entity_type: str, entity_id: str, version: str = "current") -> dict[str, str]:
    return {"label": label, "entity_type": entity_type, "entity_id": entity_id, "version": version}


def _result(
    title: str,
    body: str,
    tool_name: str,
    *,
    facts: list[dict[str, str]] | None = None,
    sources: list[dict[str, str]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    proposal: dict[str, Any] | None = None,
    navigation_hint: str = "agents",
) -> dict[str, Any]:
    return {
        "answer": {
            "title": title,
            "body": body,
            "facts": facts or [],
            "sources": sources or [],
            "navigation_hint": navigation_hint,
        },
        "tool_trace": [{"tool": tool_name, "status": "completed", "detail": "租户范围与只读权限校验通过"}],
        "evidence": evidence or [],
        "proposal": proposal,
    }


def execute_controlled_tool(
    db: Session,
    tenant_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    agent_session_id: str | None = None,
    scope_type: str = "global",
    scope_id: str | None = None,
) -> dict[str, Any]:
    """Execute one FulfillOps tool inside the deterministic business boundary.

    The function never infers tenant scope from tool arguments. The caller must
    supply a tenant that was authenticated by the Agent Gateway runtime grant.
    Proposal tools return proposal specifications only; they do not mutate an
    activity, protection state, policy, payment, or commission ledger.
    """

    if tool_name not in ALLOWED_TOOL_NAMES:
        raise ValueError("工具未注册或不在 fulfillops-safe 白名单中")
    args = arguments or {}
    normalized_scope_id = str(scope_id or "").strip().upper()

    if tool_name == "case.read":
        case_id = _required_id(args, "case_id", r"C\d{3,12}")
        if scope_type == "case" and case_id != normalized_scope_id:
            raise ValueError("案件会话只能读取当前 scope 内的案件")
        if scope_type == "activity":
            scoped_activity = db.scalar(
                select(Activity).where(
                    Activity.tenant_id == tenant_id,
                    Activity.activity_id == normalized_scope_id,
                )
            )
            if not scoped_activity or case_id not in scoped_activity.case_ids:
                raise ValueError("活动会话只能读取当前活动包含的案件")
        return build_agent_result(db, tenant_id, f"查询 {case_id} 当前状态", scope_type, scope_id)

    if tool_name == "metrics.query":
        return build_agent_result(db, tenant_id, "查询回款、计佣回款、应计佣金和实收佣金", scope_type, scope_id)

    if tool_name in {"activity.pause.propose", "activity.resume.propose"}:
        activity_id = _required_id(args, "activity_id", r"ACT-\d{3,12}")
        if scope_type == "case":
            raise ValueError("案件会话不能生成活动级行动提案")
        if scope_type == "activity" and activity_id != normalized_scope_id:
            raise ValueError("活动会话只能为当前 scope 生成行动提案")
        verb = "暂停" if tool_name == "activity.pause.propose" else "恢复"
        generated = build_agent_result(db, tenant_id, f"请{verb} {activity_id}", "activity", activity_id)
        if generated.get("proposal"):
            generated["tool_trace"][-1]["tool"] = tool_name
        return generated

    if tool_name == "activity.read":
        activity_id = _required_id(args, "activity_id", r"ACT-\d{3,12}")
        if scope_type == "case":
            raise ValueError("案件会话不能读取活动级资源")
        if scope_type == "activity" and activity_id != normalized_scope_id:
            raise ValueError("活动会话只能读取当前 scope 内的活动")
        activity = db.scalar(
            select(Activity).where(Activity.tenant_id == tenant_id, Activity.activity_id == activity_id)
        )
        if not activity:
            return _result(
                "未找到活动",
                f"当前工作空间中不存在 {activity_id}。",
                tool_name,
                sources=[_source("活动仓库", "activity", activity_id)],
                navigation_hint="activities",
            )
        return _result(
            f"{activity_id} 活动查询",
            f"活动“{activity.name}”当前为 {activity.status}，运行模式为 {activity.mode}。",
            tool_name,
            facts=[
                {"label": "资产包", "value": activity.package_id},
                {"label": "案件数", "value": str(len(activity.case_ids))},
                {"label": "策略版本", "value": f"v{activity.policy_version}"},
            ],
            sources=[_source("活动仓库", "activity", activity.activity_id)],
            evidence=[
                {
                    "activity_id": activity.activity_id,
                    "status": activity.status,
                    "mode": activity.mode,
                    "case_count": len(activity.case_ids),
                }
            ],
            navigation_hint=f"agent:{activity.activity_id}",
        )

    if tool_name == "policy.read":
        package_id = str(args.get("package_id") or "").strip().upper()
        statement = select(AssetPackage).where(AssetPackage.tenant_id == tenant_id)
        if package_id:
            if not re.fullmatch(r"PKG_[A-Z0-9_-]{1,32}", package_id):
                raise ValueError("package_id 格式无效")
            statement = statement.where(AssetPackage.package_id == package_id)
        packages = list(db.scalars(statement.order_by(AssetPackage.package_id)))
        return _result(
            "授权策略查询",
            "；".join(
                f"{item.package_id} 为 {item.policy_status}，策略 v{item.policy_version}，单案预算上限 ¥{item.budget_limit_yuan:g}"
                for item in packages
            )
            or "当前工作空间中没有符合条件的资产包策略。",
            tool_name,
            facts=[{"label": "策略数量", "value": str(len(packages))}],
            sources=[
                _source("资产包授权策略", "policy", item.package_id, f"v{item.policy_version}") for item in packages
            ],
            evidence=[
                {
                    "package_id": item.package_id,
                    "policy_status": item.policy_status,
                    "policy_version": item.policy_version,
                    "budget_limit_yuan": item.budget_limit_yuan,
                }
                for item in packages
            ],
            navigation_hint="strategy",
        )

    if tool_name == "knowledge.search":
        query = str(args.get("query") or "").strip()
        if not 1 <= len(query) <= 500:
            raise ValueError("query 长度必须为 1 到 500 个字符")
        packages = list(
            db.scalars(select(AssetPackage).where(AssetPackage.tenant_id == tenant_id).order_by(AssetPackage.package_id))
        )
        return _result(
            "知识检索结果",
            f"已在当前租户的授权策略与操作知识范围内检索“{query}”。生产环境应由版本化知识库返回命中文段；当前返回可核验的策略目录。",
            tool_name,
            facts=[{"label": "可用策略目录", "value": str(len(packages))}],
            sources=[
                _source("资产包授权策略", "policy", item.package_id, f"v{item.policy_version}") for item in packages
            ],
            evidence=[{"query": query, "matched_policy_ids": [item.package_id for item in packages]}],
            navigation_hint="strategy",
        )

    run_id = _required_id(args, "run_id", r"RUN-[A-Z0-9-]{4,64}")
    run_query = select(AgentRun).where(AgentRun.tenant_id == tenant_id, AgentRun.id == run_id)
    if agent_session_id:
        run_query = run_query.where(AgentRun.session_id == agent_session_id)
    run = db.scalar(run_query)
    if not run:
        return _result(
            "未找到运行记录",
            f"当前工作空间中不存在 {run_id}。",
            tool_name,
            sources=[_source("Agent 运行仓库", "agent_run", run_id)],
        )
    return _result(
        f"{run.id} 运行查询",
        f"运行状态为 {run.status}，Provider 为 {run.provider}，已记录 {len(run.tool_trace)} 个工具步骤。",
        tool_name,
        facts=[
            {"label": "Provider", "value": run.provider},
            {"label": "Profile", "value": run.profile},
            {"label": "工具步骤", "value": str(len(run.tool_trace))},
        ],
        sources=[_source("Agent 运行仓库", "agent_run", run.id)],
        evidence=[{"run_id": run.id, "status": run.status, "tool_trace": run.tool_trace}],
    )
