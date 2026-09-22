from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .audit import audit
from .dependencies import Context, Database
from .models import AgentRun, CaseRecord, ModelReplayRun, TenantMembership, TenantPlan
from .security import require_role

router = APIRouter(prefix="/api/v1/platform", tags=["platform-control-plane"])

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


class PlanUpdate(BaseModel):
    plan_code: Literal["pilot", "team", "enterprise"]
    expected_version: int = Field(ge=0)
    acknowledged: bool


def _plan_view(row: TenantPlan | None, tenant_id: str) -> dict:
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
    defaults = PLAN_CATALOG["team"]
    return {
        "tenant_id": tenant_id,
        "plan_code": "team",
        "status": "active",
        **defaults,
        "version": 0,
        "updated_at": None,
    }


@router.get("/subscription")
def get_subscription(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    return _plan_view(db.get(TenantPlan, context.tenant_id), context.tenant_id)


@router.put("/subscription")
def update_subscription(payload: PlanUpdate, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认套餐变更将影响租户权益和限额")
    row = db.get(TenantPlan, context.tenant_id)
    current_version = row.version if row else 0
    if current_version != payload.expected_version:
        raise HTTPException(409, "套餐版本已变化，请刷新后重试")
    config = PLAN_CATALOG[payload.plan_code]
    if row is None:
        row = TenantPlan(
            tenant_id=context.tenant_id,
            plan_code=payload.plan_code,
            status="active",
            version=1,
            updated_by=context.actor_id,
            **config,
        )
        db.add(row)
    else:
        row.plan_code = payload.plan_code
        row.seat_limit = config["seat_limit"]
        row.monthly_run_limit = config["monthly_run_limit"]
        row.monthly_budget_cents = config["monthly_budget_cents"]
        row.features = config["features"]
        row.version += 1
        row.updated_by = context.actor_id
    audit(
        db,
        context,
        "platform.subscription.updated",
        "tenant_plan",
        context.tenant_id,
        {"plan_code": payload.plan_code, "version": row.version},
    )
    db.commit()
    return _plan_view(row, context.tenant_id)


@router.get("/usage")
def get_usage(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    plan = _plan_view(db.get(TenantPlan, context.tenant_id), context.tenant_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seats = (
        db.scalar(
            select(func.count(TenantMembership.id)).where(
                TenantMembership.tenant_id == context.tenant_id, TenantMembership.status == "active"
            )
        )
        or 0
    )
    runs = (
        db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.tenant_id == context.tenant_id, AgentRun.started_at >= month_start
            )
        )
        or 0
    )
    replay_cost_usd = (
        db.scalar(
            select(func.coalesce(func.sum(ModelReplayRun.estimated_cost_usd), 0)).where(
                ModelReplayRun.tenant_id == context.tenant_id, ModelReplayRun.created_at >= month_start
            )
        )
        or 0
    )
    cases = db.scalar(select(func.count(CaseRecord.id)).where(CaseRecord.tenant_id == context.tenant_id)) or 0
    budget_used_cents = round(float(replay_cost_usd) * 700)
    return {
        "period": month_start.strftime("%Y-%m"),
        "meters": {
            "seats": {"used": seats, "limit": plan["seat_limit"]},
            "agent_runs": {"used": runs, "limit": plan["monthly_run_limit"]},
            "model_budget_cents": {"used": budget_used_cents, "limit": plan["monthly_budget_cents"]},
            "managed_cases": {"used": cases, "limit": None},
        },
        "hard_limit_reached": seats >= plan["seat_limit"]
        or runs >= plan["monthly_run_limit"]
        or budget_used_cents >= plan["monthly_budget_cents"],
    }


@router.get("/capabilities")
def get_capabilities(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    plan = _plan_view(db.get(TenantPlan, context.tenant_id), context.tenant_id)
    return {
        "tenant_id": context.tenant_id,
        "plan_code": plan["plan_code"],
        "capabilities": {
            key: key in plan["features"]
            for key in sorted({feature for item in PLAN_CATALOG.values() for feature in item["features"]})
        },
    }
