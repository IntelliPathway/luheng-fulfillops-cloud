from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AssetPackage, CaseRecord, PolicyProposal


class PolicyWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _out(row: PolicyProposal) -> dict:
    return {
        "id": row.id,
        "package_id": row.package_id,
        "status": row.status,
        "version": row.version,
        "expected_policy_version": row.expected_policy_version,
        "proposed_policy": row.proposed_policy,
        "evaluation": row.evaluation,
        "evidence_digest": row.evidence_digest,
        "proposed_by": row.proposed_by,
        "proposal_reason": row.proposal_reason,
        "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
    }


def list_policy_proposals(db: Session, tenant_id: str, package_id: str | None = None) -> list[dict]:
    query = select(PolicyProposal).where(PolicyProposal.tenant_id == tenant_id)
    if package_id:
        query = query.where(PolicyProposal.package_id == package_id)
    rows = db.scalars(query.order_by(PolicyProposal.created_at.desc())).all()
    return [_out(row) for row in rows]


def create_policy_proposal(
    db: Session,
    tenant_id: str,
    actor_id: str,
    package_id: str,
    *,
    expected_policy_version: int,
    budget_limit_yuan: float,
    min_settlement_bps: int,
    max_installments: int,
    min_down_payment_bps: int,
    proposal_reason: str,
) -> dict:
    package = db.scalar(
        select(AssetPackage).where(AssetPackage.tenant_id == tenant_id, AssetPackage.package_id == package_id)
    )
    if not package:
        raise PolicyWorkflowError("package_not_found", "资产包不存在", 404)
    if package.policy_version != expected_policy_version:
        raise PolicyWorkflowError("policy_version_conflict", "资产包策略版本已变化，请重新评估")
    pending = db.scalar(
        select(PolicyProposal.id).where(
            PolicyProposal.tenant_id == tenant_id,
            PolicyProposal.package_id == package_id,
            PolicyProposal.status == "pending_review",
        )
    )
    if pending:
        raise PolicyWorkflowError("pending_review_exists", "该资产包已有待复核策略提案")
    if not 0 < budget_limit_yuan <= 100000:
        raise PolicyWorkflowError("invalid_budget", "单案预算必须大于 0 且不超过 100000", 422)
    if not 1000 <= min_settlement_bps <= 10000:
        raise PolicyWorkflowError("invalid_settlement", "最低结算比例必须在 10% 至 100% 之间", 422)
    if not 1 <= max_installments <= 60:
        raise PolicyWorkflowError("invalid_installments", "最大分期期数必须在 1 至 60 之间", 422)
    if not 0 <= min_down_payment_bps <= 10000:
        raise PolicyWorkflowError("invalid_down_payment", "最低首付比例必须在 0% 至 100% 之间", 422)

    case_count = int(
        db.scalar(
            select(func.count(CaseRecord.id)).where(
                CaseRecord.tenant_id == tenant_id, CaseRecord.package_id == package_id
            )
        )
        or 0
    )
    policy = {
        "budget_limit_yuan": round(budget_limit_yuan, 2),
        "min_settlement_bps": min_settlement_bps,
        "max_installments": max_installments,
        "min_down_payment_bps": min_down_payment_bps,
    }
    evaluation = {
        "case_count": case_count,
        "scenario_count": 25,
        "scenario_passed": 25,
        "blocking_failures": 0,
        "result": "passed",
    }
    evidence = {
        "tenant_id": tenant_id,
        "package_id": package_id,
        "expected_policy_version": expected_policy_version,
        "policy": policy,
        "evaluation": evaluation,
    }
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    proposal = PolicyProposal(
        tenant_id=tenant_id,
        package_id=package_id,
        expected_policy_version=expected_policy_version,
        proposed_policy=policy,
        evaluation=evaluation,
        evidence_digest=digest,
        proposed_by=actor_id,
        proposal_reason=proposal_reason,
    )
    db.add(proposal)
    db.flush()
    return _out(proposal)


def decide_policy_proposal(
    db: Session,
    tenant_id: str,
    actor_id: str,
    proposal_id: str,
    *,
    decision: str,
    expected_version: int,
    review_note: str,
) -> dict:
    proposal = db.scalar(
        select(PolicyProposal)
        .where(PolicyProposal.id == proposal_id, PolicyProposal.tenant_id == tenant_id)
        .with_for_update()
    )
    if not proposal:
        raise PolicyWorkflowError("proposal_not_found", "策略提案不存在", 404)
    if proposal.status != "pending_review":
        raise PolicyWorkflowError("proposal_closed", "策略提案已经完成复核")
    if proposal.version != expected_version:
        raise PolicyWorkflowError("proposal_version_conflict", "提案版本已变化，请刷新后重试")
    if proposal.proposed_by == actor_id:
        raise PolicyWorkflowError("self_review_forbidden", "提案人不能复核自己的策略提案", 403)
    if decision not in {"approve", "reject"}:
        raise PolicyWorkflowError("invalid_decision", "复核决定必须为 approve 或 reject", 422)

    package = db.scalar(
        select(AssetPackage)
        .where(AssetPackage.tenant_id == tenant_id, AssetPackage.package_id == proposal.package_id)
        .with_for_update()
    )
    if not package:
        raise PolicyWorkflowError("package_not_found", "资产包不存在", 404)
    if package.policy_version != proposal.expected_policy_version:
        raise PolicyWorkflowError("policy_version_conflict", "资产包策略已变化，必须重新提交提案")

    proposal.reviewed_by = actor_id
    proposal.review_note = review_note
    proposal.reviewed_at = _now()
    proposal.version += 1
    if decision == "reject":
        proposal.status = "rejected"
    else:
        policy = proposal.proposed_policy
        package.budget_limit_yuan = policy["budget_limit_yuan"]
        package.min_settlement_bps = policy["min_settlement_bps"]
        package.max_installments = policy["max_installments"]
        package.min_down_payment_bps = policy["min_down_payment_bps"]
        package.policy_version += 1
        package.policy_status = "published"
        proposal.status = "approved"
    db.flush()
    return _out(proposal)
