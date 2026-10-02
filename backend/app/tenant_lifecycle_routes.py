from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .models import TenantLifecycle, TenantLifecycleProposal, TenantPlan, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/platform/lifecycle", tags=["tenant-lifecycle"])

TRANSITIONS = {
    "trial": {"active", "suspended", "closed"},
    "active": {"grace", "suspended", "closed"},
    "grace": {"active", "suspended", "closed"},
    "suspended": {"active", "closed"},
    "closed": set(),
}


class LifecycleProposalCreate(BaseModel):
    target_stage: Literal["trial", "active", "grace", "suspended", "closed"]
    expected_lifecycle_version: int = Field(ge=0)
    region: str = Field(default="ap-southeast-1", min_length=3, max_length=32)
    data_retention_days: int = Field(default=365, ge=30, le=3650)
    trial_ends_at: datetime | None = None
    contract_reference: str | None = Field(default=None, max_length=160)
    customer_success_owner: str | None = Field(default=None, max_length=120)
    proposal_reason: str = Field(min_length=8, max_length=1000)
    acknowledged: bool


class LifecycleDecision(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    review_note: str = Field(min_length=4, max_length=1000)
    acknowledged: bool


def _lifecycle_view(row: TenantLifecycle | None, tenant_id: str) -> dict:
    if row is None:
        return {
            "tenant_id": tenant_id,
            "stage": "trial",
            "region": "ap-southeast-1",
            "data_retention_days": 365,
            "trial_ends_at": None,
            "contract_reference": None,
            "customer_success_owner": None,
            "version": 0,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "tenant_id": tenant_id,
        "stage": row.stage,
        "region": row.region,
        "data_retention_days": row.data_retention_days,
        "trial_ends_at": row.trial_ends_at,
        "contract_reference": row.contract_reference,
        "customer_success_owner": row.customer_success_owner,
        "version": row.version,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def _proposal_view(row: TenantLifecycleProposal) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "current_stage": row.current_stage,
        "target_stage": row.target_stage,
        "expected_lifecycle_version": row.expected_lifecycle_version,
        "region": row.region,
        "data_retention_days": row.data_retention_days,
        "trial_ends_at": row.trial_ends_at,
        "contract_reference": row.contract_reference,
        "customer_success_owner": row.customer_success_owner,
        "proposal_reason": row.proposal_reason,
        "status": row.status,
        "version": row.version,
        "proposed_by": row.proposed_by,
        "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
    }


@router.get("")
def get_lifecycle(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    return _lifecycle_view(db.get(TenantLifecycle, context.tenant_id), context.tenant_id)


@router.get("/proposals")
def list_lifecycle_proposals(context: Context, db: Database) -> list[dict]:
    require_role(context, "admin")
    rows = db.scalars(
        select(TenantLifecycleProposal)
        .where(TenantLifecycleProposal.tenant_id == context.tenant_id)
        .order_by(TenantLifecycleProposal.created_at.desc())
    ).all()
    return [_proposal_view(row) for row in rows]


@router.post("/proposals", status_code=status.HTTP_201_CREATED)
def propose_lifecycle(payload: LifecycleProposalCreate, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认生命周期变更会影响租户访问和数据保留")
    lifecycle = db.get(TenantLifecycle, context.tenant_id)
    current = _lifecycle_view(lifecycle, context.tenant_id)
    if current["version"] != payload.expected_lifecycle_version:
        raise HTTPException(409, "租户生命周期版本已变化")
    if payload.target_stage not in TRANSITIONS[current["stage"]]:
        raise HTTPException(409, f"不允许从 {current['stage']} 转换到 {payload.target_stage}")
    if payload.target_stage == "active" and not payload.contract_reference:
        raise HTTPException(422, "激活租户必须提供合同或试点批准引用")
    pending = db.scalar(
        select(TenantLifecycleProposal.id).where(
            TenantLifecycleProposal.tenant_id == context.tenant_id,
            TenantLifecycleProposal.status == "pending_review",
        )
    )
    if pending:
        raise HTTPException(409, "已有待复核的生命周期提案")
    row = TenantLifecycleProposal(
        tenant_id=context.tenant_id,
        current_stage=current["stage"],
        proposed_by=context.actor_id,
        **payload.model_dump(exclude={"acknowledged"}),
    )
    db.add(row)
    db.flush()
    audit(
        db,
        context,
        "tenant_lifecycle.proposed",
        "tenant_lifecycle_proposal",
        row.id,
        {"from": row.current_stage, "to": row.target_stage},
    )
    db.commit()
    return _proposal_view(row)


@router.post("/proposals/{proposal_id}/decision")
def decide_lifecycle(proposal_id: str, payload: LifecycleDecision, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认已独立核验合同、保留策略和访问影响")
    proposal = db.scalar(
        select(TenantLifecycleProposal)
        .where(
            TenantLifecycleProposal.id == proposal_id,
            TenantLifecycleProposal.tenant_id == context.tenant_id,
        )
        .with_for_update()
    )
    if not proposal:
        raise HTTPException(404, "生命周期提案不存在")
    if proposal.status != "pending_review" or proposal.version != payload.expected_version:
        raise HTTPException(409, "生命周期提案已完成或版本已变化")
    if proposal.proposed_by == context.actor_id:
        raise HTTPException(409, "提案人不能复核自己的生命周期变更")
    lifecycle = db.get(TenantLifecycle, context.tenant_id)
    current = _lifecycle_view(lifecycle, context.tenant_id)
    if current["stage"] != proposal.current_stage or current["version"] != proposal.expected_lifecycle_version:
        raise HTTPException(409, "租户生命周期已变化，必须重新提交")
    if payload.decision == "approve":
        if lifecycle is None:
            lifecycle = TenantLifecycle(
                tenant_id=context.tenant_id,
                stage=proposal.target_stage,
                region=proposal.region,
                data_retention_days=proposal.data_retention_days,
                trial_ends_at=proposal.trial_ends_at,
                contract_reference=proposal.contract_reference,
                customer_success_owner=proposal.customer_success_owner,
                version=1,
                updated_by=context.actor_id,
            )
            db.add(lifecycle)
        else:
            lifecycle.stage = proposal.target_stage
            lifecycle.region = proposal.region
            lifecycle.data_retention_days = proposal.data_retention_days
            lifecycle.trial_ends_at = proposal.trial_ends_at
            lifecycle.contract_reference = proposal.contract_reference
            lifecycle.customer_success_owner = proposal.customer_success_owner
            lifecycle.version += 1
            lifecycle.updated_by = context.actor_id
        plan = db.get(TenantPlan, context.tenant_id)
        if plan:
            plan.status = "suspended" if proposal.target_stage in {"suspended", "closed"} else "active"
            plan.version += 1
            plan.updated_by = context.actor_id
        proposal.status = "approved"
    else:
        proposal.status = "rejected"
    proposal.version += 1
    proposal.reviewed_by = context.actor_id
    proposal.review_note = payload.review_note
    proposal.reviewed_at = utcnow()
    audit(
        db,
        context,
        f"tenant_lifecycle.{proposal.status}",
        "tenant_lifecycle_proposal",
        proposal.id,
        {"from": proposal.current_stage, "to": proposal.target_stage},
    )
    db.commit()
    return _proposal_view(proposal)
