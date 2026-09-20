from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .membership_governance import (
    MembershipGovernanceError,
    create_proposal,
    decide_proposal,
    list_members,
)
from .models import MembershipProposal
from .schemas import (
    MembershipProposalCreateRequest,
    MembershipProposalDecisionRequest,
    MembershipProposalOut,
    TenantMemberOut,
)
from .security import require_role

router = APIRouter(prefix="/api/v1/governance", tags=["membership-governance"])


def _raise(exc: MembershipGovernanceError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


@router.get("/members", response_model=list[TenantMemberOut])
def get_members(context: Context, db: Database) -> list[TenantMemberOut]:
    require_role(context, "admin")
    return [TenantMemberOut(**row) for row in list_members(db, context.tenant_id)]


@router.get("/membership-proposals", response_model=list[MembershipProposalOut])
def get_proposals(context: Context, db: Database) -> list[MembershipProposalOut]:
    require_role(context, "admin")
    rows = db.scalars(
        select(MembershipProposal)
        .where(MembershipProposal.tenant_id == context.tenant_id)
        .order_by(MembershipProposal.created_at.desc())
    )
    return [MembershipProposalOut.model_validate(row) for row in rows]


@router.post("/membership-proposals", response_model=MembershipProposalOut, status_code=status.HTTP_201_CREATED)
def propose_membership(
    payload: MembershipProposalCreateRequest, context: Context, db: Database
) -> MembershipProposalOut:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认成员变更将进入独立复核")
    try:
        row = create_proposal(db, context.tenant_id, context.actor_id, **payload.model_dump(exclude={"acknowledged"}))
        audit(db, context, "membership.proposed", "membership_proposal", row.id, {"target_user_id": row.target_user_id})
        db.commit()
        db.refresh(row)
        return MembershipProposalOut.model_validate(row)
    except MembershipGovernanceError as exc:
        db.rollback()
        _raise(exc)


@router.post("/membership-proposals/{proposal_id}/decision", response_model=MembershipProposalOut)
def review_membership(
    proposal_id: str, payload: MembershipProposalDecisionRequest, context: Context, db: Database
) -> MembershipProposalOut:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认已独立复核成员权限")
    try:
        row = decide_proposal(
            db,
            context.tenant_id,
            context.actor_id,
            proposal_id,
            **payload.model_dump(exclude={"acknowledged"}),
        )
        audit(
            db,
            context,
            f"membership.{row.status}",
            "membership_proposal",
            row.id,
            {"target_user_id": row.target_user_id},
        )
        db.commit()
        db.refresh(row)
        return MembershipProposalOut.model_validate(row)
    except MembershipGovernanceError as exc:
        db.rollback()
        _raise(exc)
