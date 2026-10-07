from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import ContactAttempt, ServiceConfig, TelephonyEvent
from .secret_store import SecretStoreError, resolve_secret

ALLOWED_EVENTS = {"initiated", "ringing", "answered", "completed", "failed"}


class TelephonyError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class AcceptedTelephonyEvent:
    event: TelephonyEvent
    duplicate: bool


def sign_telephony_webhook(secret: str, timestamp: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()


def accept_telephony_webhook(
    db: Session, tenant_id: str, provider: str, payload, raw_body: bytes, timestamp: str, signature: str
) -> AcceptedTelephonyEvent:
    try:
        issued_at = int(timestamp)
    except ValueError as exc:
        raise TelephonyError("invalid_timestamp", "电话事件时间戳无效", 401) from exc
    if abs(int(time.time()) - issued_at) > 300:
        raise TelephonyError("stale_webhook", "电话事件超过五分钟验签窗口", 401)
    config = db.scalar(
        select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id, ServiceConfig.service_type == "phone")
    )
    if not config or config.provider != provider or not config.connected or not config.secret_ref:
        raise TelephonyError("provider_not_ready", "电话 Provider 尚未连接或回调来源不匹配", 503)
    try:
        secret = resolve_secret(db, config.secret_ref, tenant_id, "phone")
    except SecretStoreError as exc:
        raise TelephonyError("secret_unavailable", "电话回调验签密钥不可用", 503) from exc
    if not secret:
        raise TelephonyError("secret_unavailable", "电话回调验签密钥不可用", 503)
    expected = sign_telephony_webhook(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature.lower()):
        raise TelephonyError("invalid_signature", "电话事件签名验证失败", 401)
    payload_digest = hashlib.sha256(raw_body).hexdigest()
    existing = db.scalar(
        select(TelephonyEvent).where(
            TelephonyEvent.tenant_id == tenant_id,
            TelephonyEvent.provider == provider,
            TelephonyEvent.provider_event_id == payload.event_id,
        )
    )
    if existing:
        if existing.payload_digest != payload_digest:
            raise TelephonyError("idempotency_conflict", "相同电话事件编号对应不同载荷")
        existing.duplicate_count += 1
        db.commit()
        return AcceptedTelephonyEvent(existing, True)
    if payload.event_type not in ALLOWED_EVENTS:
        raise TelephonyError("event_type_denied", "电话事件类型不受支持", 422)
    event = TelephonyEvent(
        tenant_id=tenant_id,
        provider=provider,
        provider_event_id=payload.event_id,
        call_reference=payload.call_reference,
        event_type=payload.event_type,
        status=payload.event_type,
        occurred_at=payload.occurred_at.astimezone(UTC).replace(tzinfo=None),
        case_id=payload.case_id,
        activity_id=payload.activity_id,
        failure_code=payload.failure_code,
        payload_digest=payload_digest,
        signature_digest=hashlib.sha256(signature.encode()).hexdigest(),
    )
    db.add(event)
    attempt = db.scalar(
        select(ContactAttempt).where(
            ContactAttempt.tenant_id == tenant_id,
            ContactAttempt.id == payload.call_reference,
        )
    )
    ranks = {"queued": 0, "initiated": 1, "ringing": 2, "answered": 3, "completed": 4, "failed": 4}
    if attempt:
        if payload.case_id != attempt.case_id or payload.activity_id != attempt.activity_id:
            raise TelephonyError("attempt_scope_mismatch", "电话回执与任务案件或活动不匹配")
        allowed = [state for state, rank in ranks.items()
                   if state not in {"completed", "failed"} and rank <= ranks[payload.event_type]]
        # Conditional write preserves concurrent cancellation and terminal callbacks.
        db.execute(update(ContactAttempt).where(
            ContactAttempt.id == attempt.id, ContactAttempt.tenant_id == tenant_id,
            ContactAttempt.status.in_(allowed),
            or_(ContactAttempt.last_event_at.is_(None), ContactAttempt.last_event_at <= event.occurred_at),
        ).values(status=payload.event_type, last_event_at=event.occurred_at))
    db.commit()
    db.refresh(event)
    return AcceptedTelephonyEvent(event, False)


def list_telephony_events(db: Session, tenant_id: str, limit: int = 100) -> list[TelephonyEvent]:
    return list(
        db.scalars(
            select(TelephonyEvent)
            .where(TelephonyEvent.tenant_id == tenant_id)
            .order_by(TelephonyEvent.occurred_at.desc())
            .limit(limit)
        )
    )
