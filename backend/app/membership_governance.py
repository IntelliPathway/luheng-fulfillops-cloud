from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import MembershipProposal, TenantMembership, User


class MembershipGovernanceError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def list_members(db: Session, tenant_id: str) -> list[dict]:
    rows = db.execute(
        select(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(TenantMembership.role, User.display_name)
    ).all()
    return [
        {
            "user_id": membership.user_id,
            "display_name": user.display_name,
            "email": user.email,
            "role": membership.role,
            "status": membership.status,
        }
        for membership, user in rows
    ]


def create_proposal(
    db: Session,
    tenant_id: str,
    actor_id: str,
    target_user_id: str,
    requested_role: str,
    requested_status: str,
    proposal_reason: str,
) -> MembershipProposal:
    user = db.get(User, target_user_id)
    if not user or user.status != "active":
        raise MembershipGovernanceError("user_not_found", "目标用户不存在或已停用", 404)
    current = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == target_user_id
        )
    )
    pending = db.scalar(
        select(MembershipProposal.id).where(
            MembershipProposal.tenant_id == tenant_id,
            MembershipProposal.target_user_id == target_user_id,
            MembershipProposal.status == "pending_review",
        )
    )
    if pending:
        raise MembershipGovernanceError("pending_review_exists", "该成员已有待复核变更")
    if current and current.role == requested_role and current.status == requested_status:
        raise MembershipGovernanceError("no_change", "成员角色和状态没有变化", 422)
    proposal = MembershipProposal(
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        requested_role=requested_role,
        requested_status=requested_status,
        expected_role=current.role if current else None,
        expected_status=current.status if current else None,
        proposed_by=actor_id,
        proposal_reason=proposal_reason,
    )
    db.add(proposal)
    db.flush()
    return proposal


def decide_proposal(
    db: Session,
    tenant_id: str,
    actor_id: str,
    proposal_id: str,
    decision: str,
    expected_version: int,
    review_note: str,
) -> MembershipProposal:
    proposal = db.scalar(
        select(MembershipProposal)
        .where(MembershipProposal.id == proposal_id, MembershipProposal.tenant_id == tenant_id)
        .with_for_update()
    )
    if not proposal:
        raise MembershipGovernanceError("proposal_not_found", "成员变更提案不存在", 404)
    if proposal.status != "pending_review" or proposal.version != expected_version:
        raise MembershipGovernanceError("proposal_stale", "成员变更提案已完成或版本已变化")
    if proposal.proposed_by == actor_id:
        raise MembershipGovernanceError("self_review_forbidden", "提案人不能复核自己的成员变更", 403)
    current = db.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == proposal.target_user_id,
        )
        .with_for_update()
    )
    current_role = current.role if current else None
    current_status = current.status if current else None
    if (current_role, current_status) != (proposal.expected_role, proposal.expected_status):
        raise MembershipGovernanceError("membership_stale", "成员当前状态已变化，必须重新提交提案")
    if decision == "approve":
        if (
            current
            and current.role == "admin"
            and current.status == "active"
            and (proposal.requested_role != "admin" or proposal.requested_status != "active")
        ):
            active_admins = int(
                db.scalar(
                    select(func.count(TenantMembership.id)).where(
                        TenantMembership.tenant_id == tenant_id,
                        TenantMembership.role == "admin",
                        TenantMembership.status == "active",
                    )
                )
                or 0
            )
            if active_admins <= 1:
                raise MembershipGovernanceError("last_admin_protected", "不能停用或降级最后一名管理员")
        if current:
            current.role = proposal.requested_role
            current.status = proposal.requested_status
        else:
            db.add(
                TenantMembership(
                    tenant_id=tenant_id,
                    user_id=proposal.target_user_id,
                    role=proposal.requested_role,
                    status=proposal.requested_status,
                )
            )
        proposal.status = "approved"
    else:
        proposal.status = "rejected"
    proposal.version += 1
    proposal.reviewed_by = actor_id
    proposal.review_note = review_note
    proposal.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    return proposal
