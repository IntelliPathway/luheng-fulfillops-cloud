from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from .audit import audit
from .contact_orchestration import (
    ContactOrchestrationError,
    cancel_contact_attempt,
    create_contact_attempt,
    request_handoff,
    retry_contact_attempt,
)
from .dependencies import Context, Database
from .models import ContactAttempt
from .schemas import (
    ContactAttemptCreateRequest,
    ContactAttemptOut,
    ContactCancelRequest,
    ContactHandoffRequest,
    ContactRetryRequest,
)
from .security import require_role

router = APIRouter(prefix="/api/v1/contact-attempts", tags=["contact-orchestration"])


def _raise(exc: ContactOrchestrationError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


@router.get("", response_model=list[ContactAttemptOut])
def list_attempts(context: Context, db: Database, limit: int = Query(default=100, ge=1, le=500)):
    rows = db.scalars(
        select(ContactAttempt)
        .where(ContactAttempt.tenant_id == context.tenant_id)
        .order_by(ContactAttempt.scheduled_at.desc())
        .limit(limit)
    )
    return [ContactAttemptOut.model_validate(row) for row in rows]


@router.post("", response_model=ContactAttemptOut, status_code=status.HTTP_201_CREATED)
def create_attempt(payload: ContactAttemptCreateRequest, context: Context, db: Database):
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认联系任务受合规窗口和频次门禁约束")
    try:
        row = create_contact_attempt(
            db, context.tenant_id, context.actor_id, **payload.model_dump(exclude={"acknowledged"})
        )
        audit(db, context, "contact.queued", "contact_attempt", row.id, {"case_id": row.case_id})
        db.commit()
        db.refresh(row)
        return ContactAttemptOut.model_validate(row)
    except ContactOrchestrationError as exc:
        db.rollback()
        _raise(exc)


@router.post("/{attempt_id}/handoff", response_model=ContactAttemptOut)
def handoff(attempt_id: str, payload: ContactHandoffRequest, context: Context, db: Database):
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认由人工坐席接管后续联系")
    try:
        row = request_handoff(db, context.tenant_id, context.actor_id, attempt_id, payload.reason)
        audit(db, context, "contact.handoff", "contact_attempt", row.id, {"case_id": row.case_id})
        db.commit()
        db.refresh(row)
        return ContactAttemptOut.model_validate(row)
    except ContactOrchestrationError as exc:
        db.rollback()
        _raise(exc)


@router.post("/{attempt_id}/cancel", response_model=ContactAttemptOut)
def cancel(attempt_id: str, payload: ContactCancelRequest, context: Context, db: Database):
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认停止该联系任务")
    try:
        row = cancel_contact_attempt(db, context.tenant_id, context.actor_id, attempt_id, payload.reason)
        audit(db, context, "contact.cancelled", "contact_attempt", row.id, {"case_id": row.case_id})
        db.commit()
        db.refresh(row)
        return ContactAttemptOut.model_validate(row)
    except ContactOrchestrationError as exc:
        db.rollback()
        _raise(exc)


@router.post("/{attempt_id}/retry", response_model=ContactAttemptOut, status_code=status.HTTP_201_CREATED)
def retry(attempt_id: str, payload: ContactRetryRequest, context: Context, db: Database):
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须确认重试仍受频次与时段门禁约束")
    try:
        row = retry_contact_attempt(
            db, context.tenant_id, context.actor_id, attempt_id, payload.scheduled_at, payload.reason
        )
        audit(db, context, "contact.retried", "contact_attempt", row.id, {"retry_of_id": attempt_id})
        db.commit()
        db.refresh(row)
        return ContactAttemptOut.model_validate(row)
    except ContactOrchestrationError as exc:
        db.rollback()
        _raise(exc)
