from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CaseRecord, ContactAttempt, IntegrationState, ServiceConfig


class ContactOrchestrationError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _limits() -> tuple[int, int, int]:
    try:
        daily = int(os.getenv("CONTACT_MAX_ATTEMPTS_PER_CASE_DAY", "3"))
        start = int(os.getenv("CONTACT_WINDOW_START_UTC_HOUR", "1"))
        end = int(os.getenv("CONTACT_WINDOW_END_UTC_HOUR", "13"))
    except ValueError as exc:
        raise ContactOrchestrationError("invalid_contact_policy", "联系策略环境变量无效", 503) from exc
    if not 1 <= daily <= 10 or not 0 <= start <= 23 or not 1 <= end <= 24 or start >= end:
        raise ContactOrchestrationError("invalid_contact_policy", "联系策略范围无效", 503)
    return daily, start, end


def create_contact_attempt(
    db: Session,
    tenant_id: str,
    actor_id: str,
    *,
    case_id: str,
    activity_id: str | None,
    contact_reference: str,
    scheduled_at: datetime,
) -> ContactAttempt:
    case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id))
    if not case:
        raise ContactOrchestrationError("case_not_found", "案件不存在", 404)
    if case.blocked or case.status in {"已结清", "停止联系"}:
        raise ContactOrchestrationError("case_protected", "案件保护或终态禁止联系")
    if not case.contact_basis_ref:
        raise ContactOrchestrationError("contact_basis_missing", "案件缺少联系依据引用")
    phone = db.scalar(
        select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id, ServiceConfig.service_type == "phone")
    )
    state = db.get(IntegrationState, tenant_id)
    if not phone or not phone.connected or not state or not state.enabled:
        raise ContactOrchestrationError("phone_not_ready", "电话服务尚未完成连接、自测与启用")
    daily_limit, start_hour, end_hour = _limits()
    scheduled = scheduled_at.astimezone(UTC).replace(tzinfo=None) if scheduled_at.tzinfo else scheduled_at
    if not start_hour <= scheduled.hour < end_hour:
        raise ContactOrchestrationError("outside_contact_window", "计划时间不在允许联系窗口", 422)
    day_start = scheduled.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    attempts = int(
        db.scalar(
            select(func.count(ContactAttempt.id)).where(
                ContactAttempt.tenant_id == tenant_id,
                ContactAttempt.case_id == case_id,
                ContactAttempt.scheduled_at >= day_start,
                ContactAttempt.scheduled_at < day_end,
                ContactAttempt.status != "blocked",
            )
        )
        or 0
    )
    if attempts >= daily_limit:
        raise ContactOrchestrationError("daily_frequency_exceeded", "案件当日联系次数已达到上限")
    row = ContactAttempt(
        tenant_id=tenant_id,
        case_id=case_id,
        activity_id=activity_id,
        contact_reference=contact_reference,
        scheduled_at=scheduled,
        requested_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def request_handoff(db: Session, tenant_id: str, actor_id: str, attempt_id: str, reason: str) -> ContactAttempt:
    row = db.scalar(
        select(ContactAttempt)
        .where(ContactAttempt.tenant_id == tenant_id, ContactAttempt.id == attempt_id)
        .with_for_update()
    )
    if not row:
        raise ContactOrchestrationError("attempt_not_found", "联系任务不存在", 404)
    if row.status in {"completed", "failed", "blocked"}:
        raise ContactOrchestrationError("attempt_terminal", "终态联系任务不能转人工")
    row.status = "handoff"
    row.handoff_reason = reason
    row.handoff_requested_by = actor_id
    row.handoff_requested_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    return row
