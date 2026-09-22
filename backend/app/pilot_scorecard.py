from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .financial_ledger import financial_summary
from .models import (
    Activity,
    AssetPackage,
    CaseRecord,
    PaymentReconciliation,
    PolicyProposal,
    ProtectionIncident,
    RepaymentPlan,
    TelephonyEvent,
)


def _count(db: Session, model, tenant_id: str, *filters) -> int:
    return int(db.scalar(select(func.count(model.id)).where(model.tenant_id == tenant_id, *filters)) or 0)


def build_pilot_scorecard(db: Session, tenant_id: str) -> dict:
    money = financial_summary(db, tenant_id)
    package_count = _count(db, AssetPackage, tenant_id)
    draft_packages = _count(db, AssetPackage, tenant_id, AssetPackage.policy_status != "published")
    case_count = _count(db, CaseRecord, tenant_id)
    protected_cases = _count(db, CaseRecord, tenant_id, CaseRecord.blocked.is_(True))
    running_activities = _count(db, Activity, tenant_id, Activity.status == "running")
    pending_policies = _count(db, PolicyProposal, tenant_id, PolicyProposal.status == "pending_review")
    pending_reconciliations = _count(
        db, PaymentReconciliation, tenant_id, PaymentReconciliation.status == "pending_review"
    )
    pending_plans = _count(db, RepaymentPlan, tenant_id, RepaymentPlan.status == "pending_review")
    active_protections = _count(
        db, ProtectionIncident, tenant_id, ProtectionIncident.status.in_(["open", "pending_review", "permanent_hold"])
    )
    telephony_total = _count(db, TelephonyEvent, tenant_id)
    telephony_failed = _count(db, TelephonyEvent, tenant_id, TelephonyEvent.status == "failed")
    blockers = []
    if not case_count:
        blockers.append("没有可验收案件")
    if draft_packages:
        blockers.append(f"{draft_packages} 个资产包策略尚未发布")
    if pending_policies:
        blockers.append(f"{pending_policies} 个策略提案等待复核")
    if money["pending_receipt_count"] or pending_reconciliations:
        blockers.append("存在尚未完成复核的支付回执")
    return {
        "tenant_id": tenant_id,
        "generated_at": datetime.now(UTC),
        "status": "ready" if not blockers else "attention_required",
        "portfolio": {
            "package_count": package_count,
            "case_count": case_count,
            "protected_case_count": protected_cases,
            "draft_package_count": draft_packages,
        },
        "operations": {
            "running_activity_count": running_activities,
            "active_protection_count": active_protections,
            "pending_policy_count": pending_policies,
            "pending_plan_count": pending_plans,
            "telephony_event_count": telephony_total,
            "telephony_failure_count": telephony_failed,
        },
        "money": money,
        "review_queue": {
            "pending_receipt_count": money["pending_receipt_count"],
            "pending_reconciliation_count": pending_reconciliations,
            "pending_policy_count": pending_policies,
            "pending_plan_count": pending_plans,
        },
        "blockers": blockers,
        "sources": [
            "server-authoritative asset catalog",
            "immutable recovery and commission ledgers",
            "maker-checker review queues",
            "signed telephony events",
        ],
    }


def build_release_gate(db: Session, tenant_id: str) -> dict:
    def enabled(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}

    database = db.get_bind().dialect.name
    auth_mode = os.getenv("AUTH_MODE", "development")
    cors = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
    checks = [
        {"id": "database", "label": "PostgreSQL 生产数据库", "status": database == "postgresql", "detail": database},
        {
            "id": "oidc",
            "label": "企业 OIDC",
            "status": auth_mode == "oidc" and bool(os.getenv("OIDC_ISSUER")) and bool(os.getenv("OIDC_JWKS_URL")),
            "detail": auth_mode,
        },
        {
            "id": "dev-auth",
            "label": "开发身份关闭",
            "status": not enabled("ALLOW_DEV_HEADER_AUTH", True) and not enabled("ALLOW_DEV_TOKEN", True),
            "detail": "fail-closed" if not enabled("ALLOW_DEV_HEADER_AUTH", True) else "development enabled",
        },
        {
            "id": "https",
            "label": "HTTPS 与 CORS",
            "status": bool(cors) and all(value.startswith("https://") for value in cors),
            "detail": ", ".join(cors) or "not configured",
        },
        {
            "id": "runtime-secret",
            "label": "Runtime JWT 密钥",
            "status": len(os.getenv("RUNTIME_JWT_SECRET", "")) >= 32,
            "detail": "configured" if os.getenv("RUNTIME_JWT_SECRET") else "missing",
        },
        {
            "id": "seed",
            "label": "演示数据关闭",
            "status": not enabled("SEED_DEMO_DATA"),
            "detail": "disabled" if not enabled("SEED_DEMO_DATA") else "enabled",
        },
        {
            "id": "migration",
            "label": "禁止自动建表",
            "status": not enabled("AUTO_CREATE_SCHEMA", True),
            "detail": "versioned migrations" if not enabled("AUTO_CREATE_SCHEMA", True) else "auto create enabled",
        },
    ]
    external = [
        {"id": "identity", "label": "企业身份与成员映射", "status": "manual_confirmation"},
        {"id": "compliance", "label": "联系与录音合规批准", "status": "manual_confirmation"},
        {"id": "providers", "label": "模型、通信与支付 Provider", "status": "manual_confirmation"},
        {"id": "recovery", "label": "备份恢复与告警演练", "status": "manual_confirmation"},
    ]
    passed = sum(item["status"] is True for item in checks)
    return {
        "tenant_id": tenant_id,
        "generated_at": datetime.now(UTC),
        "status": "ready_for_manual_acceptance" if passed == len(checks) else "blocked",
        "automated": {"passed": passed, "total": len(checks), "checks": checks},
        "external": external,
    }
