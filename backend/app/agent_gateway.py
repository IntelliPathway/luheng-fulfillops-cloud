from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .financial_ledger import financial_summary
from .models import (
    Activity,
    AssetPackage,
    CaseRecord,
    ServiceConfig,
)

SUPPORTED_RUNTIMES = ["Hermes Agent", "DeepSeek Harness", "LangGraph Runtime", "自研 Agent Gateway"]
TOOL_CATALOG = [
    {"name": "case.read", "risk": "read", "description": "读取当前租户案件事实与保护状态"},
    {"name": "activity.read", "risk": "read", "description": "读取活动状态、范围与服务快照"},
    {"name": "metrics.query", "risk": "read", "description": "查询带口径与来源的经营和钱指标"},
    {"name": "policy.read", "risk": "read", "description": "读取已发布授权策略版本"},
    {"name": "knowledge.search", "risk": "read", "description": "检索租户知识库并返回版本化来源"},
    {"name": "run.read", "risk": "read", "description": "读取 Agent 运行、等待条件与工具结果"},
    {"name": "activity.pause.propose", "risk": "approval", "description": "仅生成活动暂停提案"},
    {"name": "activity.resume.propose", "risk": "approval", "description": "仅生成活动恢复提案"},
]

FORBIDDEN_RUNTIME_TOOLS = [
    "shell",
    "filesystem",
    "http.unrestricted",
    "payment.write",
    "commission.write",
    "protection.release",
    "policy.publish",
]


@dataclass(frozen=True)
class GatewayProfile:
    provider: str
    profile: str
    connected: bool
    approval_policy: str
    transport: str
    session_persistence: str


def gateway_profile(db: Session, tenant_id: str) -> GatewayProfile:
    config = db.scalar(
        select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id, ServiceConfig.service_type == "agent")
    )
    if not config:
        return GatewayProfile(
            "Built-in Sandbox",
            "fulfillops-safe-query",
            True,
            "所有写操作需确认",
            "sandbox-contract",
            "database-checkpoint",
        )
    return GatewayProfile(
        provider=config.provider,
        profile=str(config.settings.get("profile") or "default"),
        connected=config.connected,
        approval_policy=str(config.settings.get("approval") or "高影响动作需确认"),
        transport=str(config.settings.get("transport") or "sandbox-contract"),
        session_persistence=str(config.settings.get("sessionPersistence") or "database-checkpoint"),
    )


def gateway_overview(db: Session, tenant_id: str) -> dict[str, Any]:
    profile = gateway_profile(db, tenant_id)
    return {
        "mode": profile.transport,
        "provider": profile.provider,
        "profile": profile.profile,
        "connected": profile.connected,
        "transport": profile.transport,
        "session_persistence": profile.session_persistence,
        "supported_runtimes": SUPPORTED_RUNTIMES,
        "tools": TOOL_CATALOG,
        "forbidden_tools": FORBIDDEN_RUNTIME_TOOLS,
        "approval_policy": profile.approval_policy,
        "safety_boundary": "Runtime 只规划、查询和生成提案；策略、状态机、账务与保护服务最终裁决",
    }


def _money(value: float) -> str:
    return f"¥{value:,.2f}".rstrip("0").rstrip(".")


def _source(label: str, entity_type: str, entity_id: str, version: str = "current") -> dict[str, str]:
    return {"label": label, "entity_type": entity_type, "entity_id": entity_id, "version": version}


def _trace(tool: str, status: str = "completed", detail: str = "租户范围与只读权限校验通过") -> dict[str, str]:
    return {"tool": tool, "status": status, "detail": detail}


def build_agent_result(
    db: Session, tenant_id: str, query: str, scope_type: str, scope_id: str | None
) -> dict[str, Any]:
    text = query.strip()
    case_match = re.search(r"C\d{3}", text, re.IGNORECASE)
    activity_match = re.search(r"ACT-\d{3}", text, re.IGNORECASE)
    scoped_activity = scope_id if scope_type == "activity" else None
    activity_id = activity_match.group(0).upper() if activity_match else scoped_activity

    if activity_id and re.search(r"暂停|恢复", text):
        activity = db.scalar(
            select(Activity).where(Activity.tenant_id == tenant_id, Activity.activity_id == activity_id)
        )
        if not activity:
            return {
                "answer": {
                    "title": "未找到可操作活动",
                    "body": f"当前工作空间中不存在 {activity_id}，未生成行动提案。",
                    "facts": [],
                    "sources": [_source("活动仓库", "activity", activity_id)],
                    "navigation_hint": "activities",
                },
                "tool_trace": [_trace("activity.read", "blocked", "活动不存在或不属于当前租户")],
                "evidence": [],
                "proposal": None,
            }
        target_status = "paused" if "暂停" in text else "running"
        action_type = "activity.pause" if target_status == "paused" else "activity.resume"
        proposal = {
            "action_type": action_type,
            "arguments": {"activity_id": activity.activity_id, "target_status": target_status},
            "required_role": "operator",
        }
        verb = "暂停" if target_status == "paused" else "恢复"
        return {
            "answer": {
                "title": f"已生成{verb}提案",
                "body": f"{activity.activity_id} 当前为“{activity.status}”。确认后服务端仍会重新校验租户、角色、活动状态与保护边界。",
                "facts": [
                    {"label": "目标活动", "value": activity.activity_id},
                    {"label": "当前状态", "value": activity.status},
                    {"label": "目标状态", "value": target_status},
                ],
                "sources": [
                    _source("活动状态", "activity", activity.activity_id),
                    _source("行动权限目录", "tool_policy", action_type, "v0.3"),
                ],
                "navigation_hint": f"agent:{activity.activity_id}",
                "proposal": {"action_type": action_type, "required_role": "operator"},
            },
            "tool_trace": [
                _trace("activity.read"),
                _trace("activity.propose", "waiting_approval", "未执行写操作；等待结构化确认"),
            ],
            "evidence": [{"activity_id": activity.activity_id, "status": activity.status, "mode": activity.mode}],
            "proposal": proposal,
        }

    if case_match:
        case_id = case_match.group(0).upper()
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id))
        if not case:
            body = f"当前工作空间中未找到 {case_id}。跨租户数据不会进入查询结果。"
            facts: list[dict[str, str]] = []
            evidence: list[dict[str, Any]] = []
        else:
            body = f"案件当前状态为“{case.status}”。" + (
                "案件处于保护暂停，Agent 不会安排新的触达。"
                if case.blocked
                else "案件可继续由策略与状态机评估下一允许动作。"
            )
            facts = [
                {"label": "资产包", "value": case.package_id},
                {"label": "已签方案", "value": "是" if case.has_signed_plan else "否"},
                {"label": "保护状态", "value": "已阻断" if case.blocked else "正常"},
            ]
            evidence = [{"case_id": case.case_id, "status": case.status, "blocked": case.blocked}]
        return {
            "answer": {
                "title": f"{case_id} 案件查询",
                "body": body,
                "facts": facts,
                "sources": [
                    _source("案件主数据", "case", case_id),
                    _source("保护状态机", "protection_state", case_id, "v0.3"),
                ],
                "navigation_hint": f"case:{case_id}" if case else "cases",
            },
            "tool_trace": [_trace("case.read")],
            "evidence": evidence,
            "proposal": None,
        }

    if re.search(r"回款|佣金|实收|结算|收入|钱|经营|ChatBI|指标", text, re.IGNORECASE):
        metrics = financial_summary(db, tenant_id)
        cash = metrics["confirmed_net_recovery_cents"] / 100
        eligible = metrics["commission_eligible_recovery_cents"] / 100
        accrued = metrics["accrued_commission_cents"] / 100
        collected = metrics["collected_commission_cents"] / 100
        body = (
            f"确认净回款 {_money(cash)}，计佣回款 {_money(eligible)}，"
            f"应计佣金 {_money(accrued)}，实际收佣 {_money(collected)}。"
            "应计佣金不等于已经结算或实际到账。"
        )
        facts = [
            {"label": "确认净回款", "value": _money(cash)},
            {"label": "计佣回款", "value": _money(eligible)},
            {"label": "应计佣金", "value": _money(accrued)},
            {"label": "实际收佣", "value": _money(collected)},
        ]
        sources = [
            _source("不可变回款账簿", "recovery_ledger", "confirmed-net"),
            _source("不可变回款账簿", "recovery_ledger", "commission-eligible"),
            _source("佣金事件账簿", "commission_ledger", "accrued"),
            _source("佣金事件账簿", "commission_ledger", "collected"),
        ]
        return {
            "answer": {
                "title": "经营与钱指标查询",
                "body": body,
                "facts": facts,
                "sources": sources,
                "navigation_hint": "payments",
                "metric_definition": "确认净回款为验签、去重、匹配并扣除退款后的现金；应计佣金按有效规则计提。",
            },
            "tool_trace": [_trace("metrics.query", detail="已应用租户范围并从不可变回款/佣金账簿汇总")],
            "evidence": [{"metric_count": 5, "scope": tenant_id, "source": "financial-ledger"}],
            "proposal": None,
        }

    if re.search(r"创建|启动|下达|安排", text):
        package_match = re.search(r"PKG_[A-Z]", text, re.IGNORECASE)
        package_id = package_match.group(0).upper() if package_match else None
        package = (
            db.scalar(
                select(AssetPackage).where(AssetPackage.tenant_id == tenant_id, AssetPackage.package_id == package_id)
            )
            if package_id
            else None
        )
        return {
            "answer": {
                "title": "已生成结构化活动草案",
                "body": "我已识别活动创建意图，但不会从聊天直接启动触达。请在创建向导中确认资产包、案件范围、目标、策略版本、预算和渠道门禁。",
                "facts": [
                    {"label": "资产包", "value": package.package_id if package else "待选择"},
                    {"label": "建议目标", "value": "已签协议履约"},
                    {"label": "执行动作", "value": "进入创建向导"},
                ],
                "sources": [
                    _source("资产包授权", "asset_package", package.package_id, f"v{package.policy_version}")
                    if package
                    else _source("活动创建规则", "workflow", "activity.create", "v0.3")
                ],
                "navigation_hint": f"create:{package.package_id}" if package else "create",
            },
            "tool_trace": [
                _trace("policy.read"),
                _trace("activity.propose", "draft_only", "聊天未执行活动创建，仅生成向导草案"),
            ],
            "evidence": [{"package_id": package.package_id, "policy_version": package.policy_version}]
            if package
            else [],
            "proposal": None,
        }

    if activity_id or re.search(r"任务|运行|活动|状态", text):
        query_stmt = select(Activity).where(Activity.tenant_id == tenant_id)
        if activity_id:
            query_stmt = query_stmt.where(Activity.activity_id == activity_id)
        rows = list(db.scalars(query_stmt.order_by(Activity.created_at.desc())))
        running = sum(1 for row in rows if row.status == "running")
        paused = sum(1 for row in rows if row.status in {"paused", "blocked"})
        return {
            "answer": {
                "title": "Agent 任务与运行查询",
                "body": f"当前查询范围内共 {len(rows)} 个活动，其中 {running} 个运行、{paused} 个暂停或阻断。",
                "facts": [
                    {"label": "活动数", "value": str(len(rows))},
                    {"label": "运行", "value": str(running)},
                    {"label": "暂停/阻断", "value": str(paused)},
                ],
                "sources": [_source("活动状态仓库", "activity_collection", tenant_id)],
                "navigation_hint": f"agent:{activity_id}" if activity_id else "agents",
            },
            "tool_trace": [_trace("activity.read")],
            "evidence": [{"activity_id": row.activity_id, "status": row.status} for row in rows[:20]],
            "proposal": None,
        }

    if re.search(r"策略|授权|分期|协商", text):
        packages = list(db.scalars(select(AssetPackage).where(AssetPackage.tenant_id == tenant_id)))
        published = sum(1 for row in packages if row.policy_status == "published")
        return {
            "answer": {
                "title": "策略与授权查询",
                "body": f"当前工作空间有 {len(packages)} 个资产包，{published} 个使用已发布策略。Agent 只能在冻结的策略版本与预算上限内生成行动。",
                "facts": [
                    {"label": "资产包", "value": str(len(packages))},
                    {"label": "已发布策略", "value": str(published)},
                    {"label": "写操作", "value": "结构化确认"},
                ],
                "sources": [
                    _source("资产包授权", "asset_package", row.package_id, f"v{row.policy_version}") for row in packages
                ],
                "navigation_hint": "strategy",
            },
            "tool_trace": [_trace("policy.read")],
            "evidence": [
                {"package_id": row.package_id, "policy_status": row.policy_status, "policy_version": row.policy_version}
                for row in packages
            ],
            "proposal": None,
        }

    case_count = db.scalar(select(func.count(CaseRecord.id)).where(CaseRecord.tenant_id == tenant_id)) or 0
    return {
        "answer": {
            "title": "履衡 AI 已连接服务端知识与工具层",
            "body": "我可以查询案件、策略、任务、运行和钱指标。所有回答保留来源；暂停或恢复等写操作只生成待确认提案。",
            "facts": [
                {"label": "租户案件", "value": str(case_count)},
                {"label": "可用工具", "value": str(len(TOOL_CATALOG))},
                {"label": "执行边界", "value": "规则引擎最终裁决"},
            ],
            "sources": [_source("Agent 工具目录", "tool_catalog", "fulfillops", "v0.3")],
            "navigation_hint": "overview",
        },
        "tool_trace": [_trace("case.read", detail="仅统计当前租户案件数量")],
        "evidence": [{"tenant_id": tenant_id, "case_count": case_count}],
        "proposal": None,
    }
