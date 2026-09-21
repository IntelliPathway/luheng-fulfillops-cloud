from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ContactAttempt,
    KnowledgeDocument,
    MembershipProposal,
    PaymentReceipt,
    PaymentReconciliation,
    PolicyProposal,
    ProtectionIncident,
    RepaymentPlan,
)

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _item(**values) -> dict:
    return values


def build_work_queue(db: Session, tenant_id: str, limit: int = 100) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    items: list[dict] = []
    memberships = db.scalars(
        select(MembershipProposal).where(
            MembershipProposal.tenant_id == tenant_id, MembershipProposal.status == "pending_review"
        )
    )
    for row in memberships:
        items.append(
            _item(
                id=row.id,
                kind="membership",
                priority="P1",
                title=f"复核 {row.target_user_id} 的成员权限",
                summary=row.proposal_reason,
                risk="权限扩大或职责中断",
                required_role="admin",
                owner=row.proposed_by,
                evidence_ref=f"membership:v{row.version}",
                due_at=None,
                created_at=row.created_at,
                route="control",
            )
        )

    policies = db.scalars(
        select(PolicyProposal).where(PolicyProposal.tenant_id == tenant_id, PolicyProposal.status == "pending_review")
    )
    for row in policies:
        items.append(
            _item(
                id=row.id,
                kind="policy",
                priority="P1",
                title=f"复核 {row.package_id} 策略 v{row.expected_policy_version + 1}",
                summary=row.proposal_reason,
                risk="改变结算、分期或预算边界",
                required_role="admin",
                owner=row.proposed_by,
                evidence_ref=row.evidence_digest[:12],
                due_at=None,
                created_at=row.created_at,
                route="strategy",
            )
        )

    knowledge = db.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.status == "pending_review"
        )
    )
    for row in knowledge:
        items.append(
            _item(
                id=row.id,
                kind="knowledge",
                priority="P2",
                title=f"发布知识 {row.document_key} v{row.version}",
                summary=row.summary,
                risk="影响 Agent 检索与解释口径",
                required_role="admin",
                owner=row.proposed_by,
                evidence_ref=row.content_digest[:12],
                due_at=None,
                created_at=row.created_at,
                route="strategy",
            )
        )

    reconciliations = db.scalars(
        select(PaymentReconciliation).where(
            PaymentReconciliation.tenant_id == tenant_id, PaymentReconciliation.status == "pending_review"
        )
    )
    for row in reconciliations:
        items.append(
            _item(
                id=row.id,
                kind="reconciliation",
                priority="P0",
                title=f"复核回执与案件 {row.proposed_case_id} 的匹配",
                summary=row.reason,
                risk="批准后原子写入回款和佣金账簿",
                required_role="admin",
                owner=row.proposed_by,
                evidence_ref=row.evidence_digest[:12],
                due_at=None,
                created_at=row.proposed_at,
                route="payments",
            )
        )

    plans = db.scalars(
        select(RepaymentPlan).where(RepaymentPlan.tenant_id == tenant_id, RepaymentPlan.status == "pending_review")
    )
    for row in plans:
        items.append(
            _item(
                id=row.id,
                kind="repayment",
                priority="P1",
                title=f"复核 {row.case_id} 的 {row.installment_count} 期履约方案",
                summary=row.proposal_reason,
                risk="改变债务人的履约时间表",
                required_role="admin",
                owner=row.proposed_by,
                evidence_ref=row.evidence_digest[:12],
                due_at=None,
                created_at=row.proposed_at,
                route="plans",
            )
        )

    protections = db.scalars(
        select(ProtectionIncident).where(
            ProtectionIncident.tenant_id == tenant_id,
            ProtectionIncident.status.in_(("open", "pending_review", "permanent_hold")),
        )
    )
    for row in protections:
        items.append(
            _item(
                id=row.id,
                kind="protection",
                priority=row.priority,
                title=f"{row.case_id} · {row.category}",
                summary=row.reason,
                risk="案件触达保持暂停",
                required_role="admin" if row.status == "pending_review" else "operator",
                owner=row.owner,
                evidence_ref=(row.evidence_digest or row.opening_digest)[:12],
                due_at=row.sla_due_at,
                created_at=row.opened_at,
                route="exceptions",
            )
        )

    contacts = db.scalars(
        select(ContactAttempt).where(
            ContactAttempt.tenant_id == tenant_id, ContactAttempt.status.in_(("handoff", "failed"))
        )
    )
    for row in contacts:
        items.append(
            _item(
                id=row.id,
                kind="contact",
                priority="P1" if row.status == "handoff" else "P2",
                title=f"{row.case_id} · {'人工接管' if row.status == 'handoff' else '联系失败'}",
                summary=row.handoff_reason or "渠道执行失败，需要核对失败代码后决定是否重试。",
                risk="错误重试可能触发频次或保护边界",
                required_role="operator",
                owner=row.handoff_requested_by or row.requested_by,
                evidence_ref=row.retry_of_id or row.id,
                due_at=row.scheduled_at,
                created_at=row.created_at,
                route="control",
            )
        )

    receipts = db.scalars(
        select(PaymentReceipt).where(
            PaymentReceipt.tenant_id == tenant_id,
            PaymentReceipt.status.in_(("unmatched", "review_required", "review_pending", "rejected")),
        )
    )
    for row in receipts:
        items.append(
            _item(
                id=row.id,
                kind="payment_exception",
                priority="P0",
                title=f"隔离回执 {row.provider_event_id}",
                summary=row.failure_code or "回执尚未关联到案件。",
                risk=f"¥{row.amount_cents / 100:,.2f} 尚未进入钱指标",
                required_role="operator",
                owner="财务复核",
                evidence_ref=row.signature_digest[:12],
                due_at=None,
                created_at=row.received_at,
                route="payments",
            )
        )

    items.sort(key=lambda row: (PRIORITY_ORDER[row["priority"]], row["due_at"] or datetime.max, row["created_at"]))
    selected = items[:limit]
    return {
        "tenant_id": tenant_id,
        "generated_at": now,
        "total": len(items),
        "summary": {priority: sum(item["priority"] == priority for item in items) for priority in PRIORITY_ORDER},
        "items": selected,
    }
