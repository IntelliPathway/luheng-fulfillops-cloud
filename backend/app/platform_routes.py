from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .audit import audit
from .dependencies import Context, Database
from .entitlements import PLAN_CATALOG, plan_view, usage_snapshot
from .models import CaseRecord, TenantPlan
from .security import require_role

router = APIRouter(prefix="/api/v1/platform", tags=["platform-control-plane"])


class PlanUpdate(BaseModel):
    plan_code: Literal["pilot", "team", "enterprise"]
    expected_version: int = Field(ge=0)
    acknowledged: bool


@router.get("/subscription")
def get_subscription(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    return plan_view(db, context.tenant_id)


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
    return plan_view(db, context.tenant_id)


@router.get("/usage")
def get_usage(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    plan = plan_view(db, context.tenant_id)
    usage = usage_snapshot(db, context.tenant_id)
    cases = db.scalar(select(func.count(CaseRecord.id)).where(CaseRecord.tenant_id == context.tenant_id)) or 0
    return {
        "period": usage["period"],
        "meters": {
            "seats": {"used": usage["seats"], "limit": plan["seat_limit"]},
            "agent_runs": {"used": usage["agent_runs"], "limit": plan["monthly_run_limit"]},
            "model_budget_cents": {"used": usage["model_budget_cents"], "limit": plan["monthly_budget_cents"]},
            "managed_cases": {"used": cases, "limit": None},
        },
        "hard_limit_reached": usage["seats"] >= plan["seat_limit"]
        or usage["agent_runs"] >= plan["monthly_run_limit"]
        or usage["model_budget_cents"] >= plan["monthly_budget_cents"],
    }


@router.get("/capabilities")
def get_capabilities(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    plan = plan_view(db, context.tenant_id)
    return {
        "tenant_id": context.tenant_id,
        "plan_code": plan["plan_code"],
        "capabilities": {
            key: key in plan["features"]
            for key in sorted({feature for item in PLAN_CATALOG.values() for feature in item["features"]})
        },
    }
