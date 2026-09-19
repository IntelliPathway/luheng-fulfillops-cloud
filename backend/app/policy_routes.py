from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from .audit import audit
from .dependencies import Context, Database
from .policy_workflow import (
    PolicyWorkflowError,
    create_policy_proposal,
    decide_policy_proposal,
    list_policy_proposals,
)
from .schemas import PolicyProposalCreateRequest, PolicyProposalDecisionRequest, PolicyProposalOut
from .security import require_role

router = APIRouter(prefix="/api/v1/policy-proposals", tags=["policy-governance"])


def _raise(exc: PolicyWorkflowError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


@router.get("", response_model=list[PolicyProposalOut])
def get_policy_proposals(
    context: Context,
    db: Database,
    package_id: str | None = Query(default=None, max_length=40),
) -> list[PolicyProposalOut]:
    return [PolicyProposalOut(**row) for row in list_policy_proposals(db, context.tenant_id, package_id)]


@router.post("/packages/{package_id}", response_model=PolicyProposalOut, status_code=status.HTTP_201_CREATED)
def propose_policy(
    package_id: str,
    payload: PolicyProposalCreateRequest,
    context: Context,
    db: Database,
) -> PolicyProposalOut:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认策略提案将进入独立复核")
    try:
        result = create_policy_proposal(
            db,
            context.tenant_id,
            context.actor_id,
            package_id,
            expected_policy_version=payload.expected_policy_version,
            budget_limit_yuan=payload.budget_limit_yuan,
            min_settlement_bps=payload.min_settlement_bps,
            max_installments=payload.max_installments,
            min_down_payment_bps=payload.min_down_payment_bps,
            proposal_reason=payload.proposal_reason,
        )
        audit(
            db,
            context,
            "policy.proposed",
            "policy_proposal",
            result["id"],
            {
                "package_id": package_id,
                "evidence_digest": result["evidence_digest"],
                "expected_policy_version": result["expected_policy_version"],
            },
        )
        db.commit()
        return PolicyProposalOut(**result)
    except PolicyWorkflowError as exc:
        db.rollback()
        _raise(exc)


@router.post("/{proposal_id}/decision", response_model=PolicyProposalOut)
def decide_policy(
    proposal_id: str,
    payload: PolicyProposalDecisionRequest,
    context: Context,
    db: Database,
) -> PolicyProposalOut:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认已独立核验策略与评估证据")
    try:
        result = decide_policy_proposal(
            db,
            context.tenant_id,
            context.actor_id,
            proposal_id,
            decision=payload.decision,
            expected_version=payload.expected_version,
            review_note=payload.review_note,
        )
        audit(
            db,
            context,
            f"policy.{result['status']}",
            "policy_proposal",
            proposal_id,
            {
                "package_id": result["package_id"],
                "evidence_digest": result["evidence_digest"],
                "reviewed_version": result["version"],
            },
        )
        db.commit()
        return PolicyProposalOut(**result)
    except PolicyWorkflowError as exc:
        db.rollback()
        _raise(exc)
