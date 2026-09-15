from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain import utcnow
from .models import (
    AssetPackage,
    AuditEvent,
    CaseFinancialProfile,
    CaseRecord,
    RecoveryLedgerEntry,
    RepaymentAllocation,
    RepaymentInstallment,
    RepaymentPlan,
)

ACTIVE_PLAN_STATUSES = {"active", "completed"}


class RepaymentPlanError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _minimum_amount(amount: int, bps: int) -> int:
    return (amount * bps + 9_999) // 10_000


def _plan_context(
    db: Session,
    tenant_id: str,
    case_id: str,
    *,
    lock: bool = False,
) -> tuple[CaseRecord, CaseFinancialProfile, AssetPackage]:
    case_query = select(CaseRecord).where(
        CaseRecord.tenant_id == tenant_id,
        CaseRecord.case_id == case_id,
    )
    profile_query = select(CaseFinancialProfile).where(
        CaseFinancialProfile.tenant_id == tenant_id,
        CaseFinancialProfile.case_id == case_id,
    )
    if lock:
        case_query = case_query.with_for_update()
        profile_query = profile_query.with_for_update()
    case = db.scalar(case_query)
    if not case:
        raise RepaymentPlanError("case_not_found", "案件不存在或不属于当前工作空间", 404)
    profile = db.scalar(profile_query)
    package_query = select(AssetPackage).where(
        AssetPackage.tenant_id == tenant_id,
        AssetPackage.package_id == case.package_id,
    )
    if lock:
        package_query = package_query.with_for_update()
    package = db.scalar(package_query)
    if not profile or not package:
        raise RepaymentPlanError("plan_context_missing", "案件缺少财务档案或有效资产包策略", 409)
    return case, profile, package


def _normalize_schedule(schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [
        {
            "installment_no": int(item["installment_no"]),
            "due_date": item["due_date"],
            "due_cents": int(item["due_cents"]),
        }
        for item in schedule
    ]
    if [item["installment_no"] for item in normalized] != list(range(1, len(normalized) + 1)):
        raise RepaymentPlanError("invalid_installment_sequence", "分期期次必须从 1 开始连续排列")
    if any(item["due_cents"] <= 0 for item in normalized):
        raise RepaymentPlanError("invalid_installment_amount", "每一期应还金额必须大于 0")
    due_dates = [item["due_date"] for item in normalized]
    if due_dates != sorted(due_dates) or len(set(due_dates)) != len(due_dates):
        raise RepaymentPlanError("invalid_due_dates", "分期应还日期必须严格递增")
    return normalized


def _validate_terms(
    case: CaseRecord,
    profile: CaseFinancialProfile,
    package: AssetPackage,
    total_cents: int,
    down_payment_cents: int,
    schedule: list[dict[str, Any]],
    signed_at: datetime,
) -> None:
    if case.blocked:
        raise RepaymentPlanError("case_protected", "案件处于保护暂停，不能激活或新建履约方案", 409)
    if profile.claim_balance_cents <= 1:
        raise RepaymentPlanError("claim_balance_missing", "案件缺少可核验的债权余额", 409)
    if total_cents > profile.claim_balance_cents:
        raise RepaymentPlanError("plan_exceeds_claim", "方案总额不能超过已核验债权余额")
    minimum_total = _minimum_amount(profile.claim_balance_cents, package.min_settlement_bps)
    if total_cents < minimum_total:
        raise RepaymentPlanError("settlement_below_policy", "方案总额低于当前资产包最低结算比例")
    if len(schedule) > package.max_installments:
        raise RepaymentPlanError("installments_exceed_policy", "分期期数超过当前资产包策略上限")
    if sum(item["due_cents"] for item in schedule) != total_cents:
        raise RepaymentPlanError("schedule_total_mismatch", "分期应还金额合计必须等于方案总额")
    if schedule[0]["due_cents"] != down_payment_cents:
        raise RepaymentPlanError("down_payment_mismatch", "第一期金额必须等于首付金额")
    minimum_down = _minimum_amount(total_cents, package.min_down_payment_bps)
    if down_payment_cents < minimum_down:
        raise RepaymentPlanError("down_payment_below_policy", "首付金额低于当前资产包策略要求")
    signed_date = signed_at.date()
    if signed_at > utcnow() + timedelta(minutes=5):
        raise RepaymentPlanError("signature_in_future", "协议签署时间不能晚于当前允许时间窗")
    if not profile.mandate_start <= signed_date <= profile.mandate_end:
        raise RepaymentPlanError("signature_outside_mandate", "协议签署时间不在有效委托期内")
    if schedule[0]["due_date"] < signed_date:
        raise RepaymentPlanError("installment_before_signature", "第一期应还日期不能早于协议签署日期")


def create_plan_proposal(
    db: Session,
    tenant_id: str,
    case_id: str,
    plan_id: str,
    total_cents: int,
    down_payment_cents: int,
    schedule: list[dict[str, Any]],
    agreement_reference: str,
    agreement_digest: str,
    signed_at: datetime,
    proposal_reason: str,
    actor_id: str,
) -> RepaymentPlan:
    case_id = case_id.strip().upper()
    plan_id = plan_id.strip().upper()
    normalized_schedule = _normalize_schedule(schedule)
    signed_at = _naive_utc(signed_at)
    case, profile, package = _plan_context(db, tenant_id, case_id, lock=True)
    _validate_terms(case, profile, package, total_cents, down_payment_cents, normalized_schedule, signed_at)

    policy_snapshot = {
        "package_id": package.package_id,
        "policy_version": package.policy_version,
        "min_settlement_bps": package.min_settlement_bps,
        "max_installments": package.max_installments,
        "min_down_payment_bps": package.min_down_payment_bps,
        "mandate_start": profile.mandate_start.isoformat(),
        "mandate_end": profile.mandate_end.isoformat(),
    }
    evidence_digest = _digest(
        {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "plan_id": plan_id,
            "claim_balance_cents": profile.claim_balance_cents,
            "total_cents": total_cents,
            "down_payment_cents": down_payment_cents,
            "schedule": normalized_schedule,
            "agreement_reference": agreement_reference.strip().upper(),
            "agreement_digest": agreement_digest.lower(),
            "signed_at": signed_at.isoformat(),
            "policy_snapshot": policy_snapshot,
            "proposal_reason": proposal_reason.strip(),
        }
    )
    existing = db.scalar(
        select(RepaymentPlan)
        .where(
            RepaymentPlan.tenant_id == tenant_id,
            RepaymentPlan.plan_id == plan_id,
        )
        .with_for_update()
    )
    if existing:
        if existing.evidence_digest == evidence_digest:
            return existing
        raise RepaymentPlanError("plan_id_conflict", "同一方案编号已绑定不同条款或证据", 409)
    in_progress = db.scalar(
        select(RepaymentPlan)
        .where(
            RepaymentPlan.tenant_id == tenant_id,
            RepaymentPlan.case_id == case_id,
            RepaymentPlan.status.in_({"pending_review", *ACTIVE_PLAN_STATUSES}),
        )
        .with_for_update()
    )
    if in_progress:
        raise RepaymentPlanError("case_plan_in_progress", "案件已有待复核或执行中的履约方案", 409)

    now = utcnow()
    plan = RepaymentPlan(
        tenant_id=tenant_id,
        plan_id=plan_id,
        case_id=case_id,
        status="pending_review",
        version=1,
        currency="CNY",
        claim_balance_cents=profile.claim_balance_cents,
        total_cents=total_cents,
        down_payment_cents=down_payment_cents,
        installment_count=len(normalized_schedule),
        policy_version=package.policy_version,
        policy_snapshot=policy_snapshot,
        agreement_reference=agreement_reference.strip().upper(),
        agreement_digest=agreement_digest.lower(),
        signed_at=signed_at,
        evidence_digest=evidence_digest,
        proposal_reason=proposal_reason.strip(),
        proposed_by=actor_id,
        proposed_at=now,
    )
    db.add(plan)
    db.flush()
    for item in normalized_schedule:
        db.add(
            RepaymentInstallment(
                tenant_id=tenant_id,
                plan_row_id=plan.id,
                installment_id=f"{plan_id}-{item['installment_no']:02d}",
                installment_no=item["installment_no"],
                due_date=item["due_date"],
                due_cents=item["due_cents"],
            )
        )
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="repayment_plan.proposed",
            resource_type="repayment_plan",
            resource_id=plan.id,
            detail={
                "plan_id": plan_id,
                "case_id": case_id,
                "policy_version": package.policy_version,
                "installment_count": len(normalized_schedule),
                "evidence_digest": evidence_digest,
                "agreement_digest": agreement_digest.lower(),
            },
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RepaymentPlanError("plan_write_conflict", "方案编号或案件状态发生并发冲突", 409) from exc
    db.refresh(plan)
    return plan


def _installments(db: Session, plan: RepaymentPlan, *, lock: bool = False) -> list[RepaymentInstallment]:
    statement = (
        select(RepaymentInstallment)
        .where(
            RepaymentInstallment.tenant_id == plan.tenant_id,
            RepaymentInstallment.plan_row_id == plan.id,
        )
        .order_by(RepaymentInstallment.installment_no)
    )
    if lock:
        statement = statement.with_for_update()
    return list(db.scalars(statement))


def decide_plan(
    db: Session,
    plan: RepaymentPlan,
    decision: str,
    review_note: str,
    expected_version: int,
    actor_id: str,
) -> RepaymentPlan:
    if plan.status != "pending_review":
        raise RepaymentPlanError("plan_not_pending", "当前方案不在待复核状态", 409)
    if plan.version != expected_version:
        raise RepaymentPlanError("plan_version_conflict", "方案版本已变化，请刷新后重新复核", 409)
    if plan.proposed_by == actor_id:
        raise RepaymentPlanError("maker_checker_conflict", "方案提案人不能审批自己的提案", 409)
    if decision not in {"approve", "reject"}:
        raise RepaymentPlanError("invalid_decision", "方案复核决定无效")

    now = utcnow()
    plan.reviewed_by = actor_id
    plan.reviewed_at = now
    plan.review_note = review_note.strip()
    plan.version += 1
    case, profile, package = _plan_context(db, plan.tenant_id, plan.case_id, lock=True)
    rows = _installments(db, plan, lock=True)
    schedule = [
        {"installment_no": row.installment_no, "due_date": row.due_date, "due_cents": row.due_cents} for row in rows
    ]
    if decision == "approve":
        if package.policy_version != plan.policy_version:
            raise RepaymentPlanError("policy_version_conflict", "资产包策略版本已变化，必须重新生成方案", 409)
        _validate_terms(
            case,
            profile,
            package,
            plan.total_cents,
            plan.down_payment_cents,
            schedule,
            plan.signed_at,
        )
        active = db.scalar(
            select(RepaymentPlan)
            .where(
                RepaymentPlan.tenant_id == plan.tenant_id,
                RepaymentPlan.case_id == plan.case_id,
                RepaymentPlan.status.in_(ACTIVE_PLAN_STATUSES),
                RepaymentPlan.id != plan.id,
            )
            .with_for_update()
        )
        if active:
            raise RepaymentPlanError("active_plan_exists", "案件已经存在生效中的履约方案", 409)
        plan.status = "active"
        plan.activated_at = now
        case.has_signed_plan = True
        case.status = "履约中"
        profile.signed_plan_at = plan.signed_at.date()
        profile.signed_plan_last_due = rows[-1].due_date
        profile.signed_plan_tail_eligible = True
    else:
        plan.status = "rejected"

    db.add(
        AuditEvent(
            tenant_id=plan.tenant_id,
            actor_id=actor_id,
            action=f"repayment_plan.{decision}",
            resource_type="repayment_plan",
            resource_id=plan.id,
            detail={
                "plan_id": plan.plan_id,
                "case_id": plan.case_id,
                "version": plan.version,
                "evidence_digest": plan.evidence_digest,
                "activated": decision == "approve",
            },
        )
    )
    db.commit()
    db.refresh(plan)
    return plan


def _status_for(row: RepaymentInstallment, as_of: date) -> str:
    if row.paid_cents >= row.due_cents:
        return "paid"
    if row.paid_cents > 0:
        return "partial_overdue" if row.due_date < as_of else "partial"
    if row.due_date < as_of:
        return "overdue"
    if row.due_date == as_of:
        return "due"
    return "scheduled"


def _sync_plan_state(db: Session, plan: RepaymentPlan, rows: list[RepaymentInstallment]) -> None:
    as_of = utcnow().date()
    for row in rows:
        row.status = _status_for(row, as_of)
    paid = sum(row.paid_cents for row in rows)
    case = db.scalar(
        select(CaseRecord).where(
            CaseRecord.tenant_id == plan.tenant_id,
            CaseRecord.case_id == plan.case_id,
        )
    )
    if paid >= plan.total_cents:
        plan.status = "completed"
        plan.completed_at = plan.completed_at or utcnow()
        if case and not case.blocked:
            case.status = "已结清"
    elif plan.status == "completed":
        plan.status = "active"
        plan.completed_at = None
        if case and not case.blocked:
            case.status = "履约中"


def _allocation_label(plan: RepaymentPlan, allocations: list[RepaymentAllocation], unallocated: int) -> str:
    allocated = sum(row.amount_cents for row in allocations)
    direction = "冲销" if allocated < 0 else "分摊"
    label = f"{plan.plan_id} · {len(allocations)} 期{direction} {abs(allocated) / 100:,.2f} 元"
    if unallocated:
        label += f" · 未分摊 {abs(unallocated) / 100:,.2f} 元"
    return label


def allocate_recovery_to_plan(db: Session, recovery: RecoveryLedgerEntry) -> list[RepaymentAllocation]:
    existing = list(
        db.scalars(
            select(RepaymentAllocation).where(
                RepaymentAllocation.tenant_id == recovery.tenant_id,
                RepaymentAllocation.recovery_entry_id == recovery.entry_id,
            )
        )
    )
    if existing:
        return existing
    originals: list[RepaymentAllocation] = []
    if recovery.event_type == "REFUND" and recovery.original_entry_id:
        originals = list(
            db.scalars(
                select(RepaymentAllocation).where(
                    RepaymentAllocation.tenant_id == recovery.tenant_id,
                    RepaymentAllocation.recovery_entry_id == recovery.original_entry_id,
                    RepaymentAllocation.amount_cents > 0,
                )
            )
        )
        plan = (
            db.scalar(select(RepaymentPlan).where(RepaymentPlan.id == originals[0].plan_row_id).with_for_update())
            if originals
            else None
        )
    else:
        plan = db.scalar(
            select(RepaymentPlan)
            .where(
                RepaymentPlan.tenant_id == recovery.tenant_id,
                RepaymentPlan.case_id == recovery.case_id,
                RepaymentPlan.status.in_(ACTIVE_PLAN_STATUSES),
                RepaymentPlan.signed_at <= recovery.booked_at,
            )
            .order_by(RepaymentPlan.activated_at.desc())
            .with_for_update()
        )
    if not plan:
        return []
    rows = _installments(db, plan, lock=True)
    installment_order = {row.id: row.installment_no for row in rows}
    originals.sort(key=lambda row: installment_order.get(row.installment_row_id, 0), reverse=True)
    allocations: list[RepaymentAllocation] = []
    remaining = recovery.amount_cents
    if recovery.event_type == "PAYMENT" and remaining > 0:
        for row in rows:
            available = max(0, row.due_cents - row.paid_cents)
            amount = min(remaining, available)
            if amount <= 0:
                continue
            allocation = RepaymentAllocation(
                tenant_id=recovery.tenant_id,
                allocation_id=f"RAL-{hashlib.sha256(f'{recovery.entry_id}:{row.installment_id}'.encode()).hexdigest()[:24].upper()}",
                recovery_entry_id=recovery.entry_id,
                plan_row_id=plan.id,
                installment_row_id=row.id,
                amount_cents=amount,
            )
            db.add(allocation)
            allocations.append(allocation)
            row.paid_cents += amount
            row.last_payment_at = recovery.booked_at
            remaining -= amount
            if remaining == 0:
                break
    elif recovery.event_type == "REFUND" and remaining < 0 and recovery.original_entry_id:
        needed = abs(remaining)
        for original in originals:
            prior_reversals = list(
                db.scalars(
                    select(RepaymentAllocation).where(
                        RepaymentAllocation.tenant_id == recovery.tenant_id,
                        RepaymentAllocation.source_allocation_id == original.id,
                    )
                )
            )
            refundable = max(0, original.amount_cents + sum(row.amount_cents for row in prior_reversals))
            amount = min(needed, refundable)
            if amount <= 0:
                continue
            installment = next((row for row in rows if row.id == original.installment_row_id), None)
            if not installment:
                continue
            allocation = RepaymentAllocation(
                tenant_id=recovery.tenant_id,
                allocation_id=f"RAL-{hashlib.sha256(f'{recovery.entry_id}:{installment.installment_id}'.encode()).hexdigest()[:24].upper()}",
                recovery_entry_id=recovery.entry_id,
                plan_row_id=plan.id,
                installment_row_id=installment.id,
                amount_cents=-amount,
                source_allocation_id=original.id,
            )
            db.add(allocation)
            allocations.append(allocation)
            installment.paid_cents = max(0, installment.paid_cents - amount)
            installment.last_payment_at = recovery.booked_at
            needed -= amount
            if needed == 0:
                break
        remaining = -needed
    if allocations:
        db.flush()
        _sync_plan_state(db, plan, rows)
        recovery.allocation = _allocation_label(plan, allocations, remaining)
    return allocations


def plan_view(db: Session, plan: RepaymentPlan) -> dict[str, Any]:
    rows = _installments(db, plan)
    as_of = utcnow().date()
    installments = []
    for row in rows:
        status = _status_for(row, as_of) if plan.status in ACTIVE_PLAN_STATUSES else row.status
        installments.append(
            {
                "installment_id": row.installment_id,
                "installment_no": row.installment_no,
                "due_date": row.due_date,
                "due_cents": row.due_cents,
                "paid_cents": row.paid_cents,
                "remaining_cents": max(0, row.due_cents - row.paid_cents),
                "status": status,
                "last_payment_at": row.last_payment_at,
            }
        )
    paid_cents = sum(item["paid_cents"] for item in installments)
    overdue_cents = sum(
        item["remaining_cents"] for item in installments if item["status"] in {"overdue", "partial_overdue"}
    )
    return {
        "id": plan.id,
        "plan_id": plan.plan_id,
        "case_id": plan.case_id,
        "status": plan.status,
        "version": plan.version,
        "currency": plan.currency,
        "claim_balance_cents": plan.claim_balance_cents,
        "total_cents": plan.total_cents,
        "down_payment_cents": plan.down_payment_cents,
        "installment_count": plan.installment_count,
        "policy_version": plan.policy_version,
        "policy_snapshot": plan.policy_snapshot,
        "agreement_reference": plan.agreement_reference,
        "agreement_digest": plan.agreement_digest,
        "signed_at": plan.signed_at,
        "evidence_digest": plan.evidence_digest,
        "proposal_reason": plan.proposal_reason,
        "proposed_by": plan.proposed_by,
        "proposed_at": plan.proposed_at,
        "reviewed_by": plan.reviewed_by,
        "reviewed_at": plan.reviewed_at,
        "review_note": plan.review_note,
        "activated_at": plan.activated_at,
        "completed_at": plan.completed_at,
        "paid_cents": paid_cents,
        "remaining_cents": max(0, plan.total_cents - paid_cents),
        "overdue_cents": overdue_cents,
        "installments": installments,
    }


def repayment_overview(db: Session, tenant_id: str) -> dict[str, Any]:
    plans = list(
        db.scalars(
            select(RepaymentPlan).where(RepaymentPlan.tenant_id == tenant_id).order_by(RepaymentPlan.proposed_at.desc())
        )
    )
    views = [plan_view(db, plan) for plan in plans]
    today = utcnow().date()
    due_limit = today + timedelta(days=7)
    return {
        "active_count": sum(item["status"] == "active" for item in views),
        "pending_review_count": sum(item["status"] == "pending_review" for item in views),
        "completed_count": sum(item["status"] == "completed" for item in views),
        "overdue_plan_count": sum(item["overdue_cents"] > 0 for item in views),
        "due_soon_count": sum(
            installment["remaining_cents"] > 0 and today <= installment["due_date"] <= due_limit
            for item in views
            if item["status"] in ACTIVE_PLAN_STATUSES
            for installment in item["installments"]
        ),
        "plans": views,
    }
