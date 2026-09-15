from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain import utcnow
from .models import Activity, AuditEvent, CaseRecord, ProtectionIncident

ACTIVE_PROTECTION_STATUSES = {"open", "pending_review", "permanent_hold"}

CATEGORY_POLICIES: dict[str, dict[str, Any]] = {
    "debt_dispute": {
        "priority": "P0",
        "release_policy": "maker_checker",
        "case_status": "异议暂停",
        "default_sla_hours": 4,
    },
    "stop_contact": {
        "priority": "P0",
        "release_policy": "permanent_hold",
        "case_status": "停止联系",
        "default_sla_hours": None,
    },
    "identity_conflict": {
        "priority": "P0",
        "release_policy": "maker_checker",
        "case_status": "身份待核",
        "default_sla_hours": 4,
    },
    "mandate_expired": {
        "priority": "P1",
        "release_policy": "renewal_evidence",
        "case_status": "委托到期",
        "default_sla_hours": 24,
    },
    "data_quality": {
        "priority": "P1",
        "release_policy": "maker_checker",
        "case_status": "资料待补",
        "default_sla_hours": 24,
    },
    "amount_verification": {
        "priority": "P1",
        "release_policy": "maker_checker",
        "case_status": "金额待核",
        "default_sla_hours": 8,
    },
    "authorization_gap": {
        "priority": "P0",
        "release_policy": "maker_checker",
        "case_status": "授权待补",
        "default_sla_hours": 4,
    },
    "contact_data": {
        "priority": "P2",
        "release_policy": "maker_checker",
        "case_status": "号码缺失",
        "default_sla_hours": 24,
    },
    "budget_exhausted": {
        "priority": "P2",
        "release_policy": "maker_checker",
        "case_status": "预算耗尽",
        "default_sla_hours": 8,
    },
    "channel_failure": {
        "priority": "P2",
        "release_policy": "maker_checker",
        "case_status": "渠道异常",
        "default_sla_hours": 8,
    },
}


class ProtectionWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalized_evidence_refs(evidence_refs: list[str]) -> list[str]:
    return sorted({reference.strip().upper() for reference in evidence_refs if reference.strip()})


def open_protection_incident(
    db: Session,
    tenant_id: str,
    case_id: str,
    source_event_id: str,
    category: str,
    reason: str,
    owner: str,
    sla_hours: int | None,
    actor_id: str,
) -> ProtectionIncident:
    policy = CATEGORY_POLICIES.get(category)
    if not policy:
        raise ProtectionWorkflowError("invalid_protection_category", "保护事件类别无效")
    case = db.scalar(
        select(CaseRecord).where(
            CaseRecord.tenant_id == tenant_id,
            CaseRecord.case_id == case_id,
        ).with_for_update()
    )
    if not case:
        raise ProtectionWorkflowError("case_not_found", "案件不存在或不属于当前工作空间", 404)

    normalized_reason = reason.strip()
    normalized_owner = owner.strip()
    normalized_source = source_event_id.strip()
    opening_digest = _digest(
        {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "source_event_id": normalized_source,
            "category": category,
            "reason": normalized_reason,
            "owner": normalized_owner,
            "sla_hours": sla_hours,
        }
    )
    existing = db.scalar(
        select(ProtectionIncident).where(
            ProtectionIncident.tenant_id == tenant_id,
            ProtectionIncident.source_event_id == normalized_source,
        ).with_for_update()
    )
    if existing:
        if existing.opening_digest == opening_digest:
            return existing
        raise ProtectionWorkflowError(
            "protection_event_conflict",
            "同一来源事件已用于不同的保护事实",
            409,
        )

    now = utcnow()
    due_hours = sla_hours if sla_hours is not None else policy["default_sla_hours"]
    incident = ProtectionIncident(
        tenant_id=tenant_id,
        case_id=case_id,
        source_event_id=normalized_source,
        category=category,
        priority=policy["priority"],
        reason=normalized_reason,
        opening_digest=opening_digest,
        owner=normalized_owner,
        release_policy=policy["release_policy"],
        status="permanent_hold" if policy["release_policy"] == "permanent_hold" else "open",
        previous_case_status=case.status,
        sla_due_at=now + timedelta(hours=due_hours) if due_hours else None,
        opened_by=actor_id,
        opened_at=now,
    )
    db.add(incident)
    case.blocked = True
    case.status = policy["case_status"]

    affected_activities = 0
    activities = db.scalars(
        select(Activity).where(
            Activity.tenant_id == tenant_id,
            Activity.status.in_({"running", "paused"}),
        )
    )
    for activity in activities:
        if case_id not in activity.case_ids:
            continue
        activity.status = "blocked"
        activity.preflight = {
            **(activity.preflight or {}),
            "protection_event_id": normalized_source,
            "protection_category": category,
        }
        affected_activities += 1

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ProtectionWorkflowError(
            "protection_event_conflict",
            "保护来源事件已被其他请求接收",
            409,
        ) from exc
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="protection.incident.opened",
            resource_type="protection_incident",
            resource_id=incident.id,
            detail={
                "case_id": case_id,
                "source_event_id": normalized_source,
                "category": category,
                "priority": incident.priority,
                "release_policy": incident.release_policy,
                "opening_digest": opening_digest,
                "affected_activities": affected_activities,
            },
        )
    )
    db.commit()
    db.refresh(incident)
    return incident


def propose_protection_resolution(
    db: Session,
    incident: ProtectionIncident,
    resolution_note: str,
    evidence_refs: list[str],
    actor_id: str,
) -> ProtectionIncident:
    if incident.release_policy == "permanent_hold":
        raise ProtectionWorkflowError(
            "permanent_protection",
            "停止联系保护不能通过通用异常流程解除",
            409,
        )
    if incident.status == "resolved":
        raise ProtectionWorkflowError("protection_already_resolved", "保护事件已经完成复核", 409)
    note = resolution_note.strip()
    references = _normalized_evidence_refs(evidence_refs)
    if not references:
        raise ProtectionWorkflowError("evidence_required", "解除提案必须包含证据引用")
    if incident.release_policy == "renewal_evidence" and not any(
        reference.startswith("MANDATE-") for reference in references
    ):
        raise ProtectionWorkflowError(
            "mandate_renewal_evidence_required",
            "委托到期保护必须引用 MANDATE- 开头的续期授权证据",
        )
    if incident.status == "pending_review":
        if (
            incident.proposed_by == actor_id
            and incident.resolution_note == note
            and incident.evidence_refs == references
        ):
            return incident
        raise ProtectionWorkflowError("resolution_review_in_progress", "该保护事件已有待复核提案", 409)
    if incident.status != "open":
        raise ProtectionWorkflowError("protection_not_reviewable", "当前保护状态不能提交解除提案", 409)

    now = utcnow()
    version = incident.version + 1
    evidence_digest = _digest(
        {
            "incident_id": incident.id,
            "opening_digest": incident.opening_digest,
            "resolution_note": note,
            "evidence_refs": references,
            "version": version,
        }
    )
    incident.status = "pending_review"
    incident.version = version
    incident.resolution_note = note
    incident.evidence_refs = references
    incident.evidence_digest = evidence_digest
    incident.proposed_by = actor_id
    incident.proposed_at = now
    incident.reviewed_by = None
    incident.reviewed_at = None
    incident.review_note = None
    incident.resolved_at = None
    incident.case_released = False
    db.add(
        AuditEvent(
            tenant_id=incident.tenant_id,
            actor_id=actor_id,
            action="protection.resolution.proposed",
            resource_type="protection_incident",
            resource_id=incident.id,
            detail={
                "case_id": incident.case_id,
                "version": version,
                "evidence_digest": evidence_digest,
                "evidence_count": len(references),
                "resolution_note_digest": hashlib.sha256(note.encode()).hexdigest(),
            },
        )
    )
    db.commit()
    db.refresh(incident)
    return incident


def decide_protection_resolution(
    db: Session,
    incident: ProtectionIncident,
    decision: str,
    review_note: str,
    expected_version: int,
    actor_id: str,
) -> ProtectionIncident:
    if incident.status != "pending_review":
        raise ProtectionWorkflowError("resolution_not_pending", "解除提案已经处理或不再有效", 409)
    if incident.version != expected_version:
        raise ProtectionWorkflowError("resolution_version_conflict", "解除提案已更新，请刷新后重试", 409)
    if incident.proposed_by == actor_id:
        raise ProtectionWorkflowError("maker_checker_conflict", "提案人与复核人必须是不同账号", 409)
    if decision not in {"approve", "reject"}:
        raise ProtectionWorkflowError("invalid_resolution_decision", "复核决定无效")

    case = db.scalar(
        select(CaseRecord).where(
            CaseRecord.tenant_id == incident.tenant_id,
            CaseRecord.case_id == incident.case_id,
        ).with_for_update()
    )
    if not case:
        raise ProtectionWorkflowError("case_not_found", "保护事件关联案件不存在", 409)

    now = utcnow()
    incident.version += 1
    incident.reviewed_by = actor_id
    incident.reviewed_at = now
    incident.review_note = review_note.strip()
    incident.case_released = False
    if decision == "reject":
        incident.status = "open"
    else:
        incident.status = "resolved"
        incident.resolved_at = now
        db.flush()
        remaining = db.scalar(
            select(ProtectionIncident.id).where(
                ProtectionIncident.tenant_id == incident.tenant_id,
                ProtectionIncident.case_id == incident.case_id,
                ProtectionIncident.id != incident.id,
                ProtectionIncident.status.in_(ACTIVE_PROTECTION_STATUSES),
            ).limit(1)
        )
        if not remaining:
            case.blocked = False
            case.status = "待重新评估"
            incident.case_released = True

    db.add(
        AuditEvent(
            tenant_id=incident.tenant_id,
            actor_id=actor_id,
            action=f"protection.resolution.{decision}",
            resource_type="protection_incident",
            resource_id=incident.id,
            detail={
                "case_id": incident.case_id,
                "version": incident.version,
                "evidence_digest": incident.evidence_digest,
                "review_note_digest": hashlib.sha256(review_note.strip().encode()).hexdigest(),
                "case_released": incident.case_released,
                "activity_resume": False,
            },
        )
    )
    db.commit()
    db.refresh(incident)
    return incident


def protection_overview(db: Session, tenant_id: str) -> dict[str, Any]:
    incidents = list(
        db.scalars(
            select(ProtectionIncident)
            .where(ProtectionIncident.tenant_id == tenant_id)
            .order_by(ProtectionIncident.opened_at.desc())
        )
    )
    active = [row for row in incidents if row.status in ACTIVE_PROTECTION_STATUSES]
    now = utcnow()
    return {
        "active_count": len(active),
        "p0_count": sum(row.priority == "P0" for row in active),
        "pending_review_count": sum(row.status == "pending_review" for row in active),
        "overdue_count": sum(bool(row.sla_due_at and row.sla_due_at < now) for row in active),
        "permanent_hold_count": sum(row.status == "permanent_hold" for row in active),
        "incidents": incidents,
    }
