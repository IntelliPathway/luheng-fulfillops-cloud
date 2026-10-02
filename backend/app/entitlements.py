from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import TenantMembership, TenantPlan, UsageEvent

PLAN_CATALOG = {
    "pilot": {
        "seat_limit": 5,
        "monthly_run_limit": 500,
        "monthly_budget_cents": 50000,
        "features": ["agents", "ledger"],
    },
    "team": {
        "seat_limit": 25,
        "monthly_run_limit": 5000,
        "monthly_budget_cents": 500000,
        "features": ["agents", "ledger", "experiments", "multichannel", "harness-selector"],
    },
    "enterprise": {
        "seat_limit": 500,
        "monthly_run_limit": 100000,
        "monthly_budget_cents": 20000000,
        "features": [
            "agents",
            "ledger",
            "experiments",
            "multichannel",
            "harness-selector",
            "enterprise-oidc",
            "audit-export",
        ],
    },
}


class EntitlementError(ValueError):
    def __init__(self, code: str, message: str, http_status: int = 403):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def plan_view(db: Session, tenant_id: str) -> dict:
    row = db.get(TenantPlan, tenant_id)
    if row:
        return {
            "tenant_id": tenant_id,
            "plan_code": row.plan_code,
            "status": row.status,
            "seat_limit": row.seat_limit,
            "monthly_run_limit": row.monthly_run_limit,
            "monthly_budget_cents": row.monthly_budget_cents,
            "features": row.features,
            "version": row.version,
            "updated_at": row.updated_at,
        }
    return {
        "tenant_id": tenant_id,
        "plan_code": "team",
        "status": "active",
        **PLAN_CATALOG["team"],
        "version": 0,
        "updated_at": None,
    }


def month_start() -> datetime:
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_snapshot(db: Session, tenant_id: str) -> dict:
    start = month_start()
    seats = int(
        db.scalar(
            select(func.count(TenantMembership.id)).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
            )
        )
        or 0
    )
    runs = int(
        db.scalar(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.meter == "agent_run",
                UsageEvent.occurred_at >= start,
            )
        )
        or 0
    )
    model_budget_cents = int(
        db.scalar(
            select(func.coalesce(func.sum(UsageEvent.amount_cents), 0)).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.meter == "model_token",
                UsageEvent.occurred_at >= start,
            )
        )
        or 0
    )
    return {
        "period": start.strftime("%Y-%m"),
        "seats": seats,
        "agent_runs": runs,
        "model_budget_cents": model_budget_cents,
    }


def require_capability(db: Session, tenant_id: str, capability: str) -> dict:
    plan = plan_view(db, tenant_id)
    if plan["status"] != "active":
        raise EntitlementError("subscription_inactive", "当前租户套餐已暂停")
    if capability not in plan["features"]:
        raise EntitlementError("capability_not_entitled", f"当前套餐未开通能力：{capability}")
    return plan


def require_capacity(db: Session, tenant_id: str, meter: str, additional: int = 1) -> dict:
    plan = plan_view(db, tenant_id)
    if plan["status"] != "active":
        raise EntitlementError("subscription_inactive", "当前租户套餐已暂停")
    usage = usage_snapshot(db, tenant_id)
    limit_key = {
        "seats": "seat_limit",
        "agent_runs": "monthly_run_limit",
        "model_budget_cents": "monthly_budget_cents",
    }[meter]
    if usage[meter] + additional > plan[limit_key]:
        raise EntitlementError("quota_exceeded", f"{meter} 已达到当前套餐限额", 429)
    return {"plan": plan, "usage": usage}
