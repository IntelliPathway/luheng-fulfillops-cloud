from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from .dependencies import Context, Database
from .models import AuditEvent
from .schemas import AuditEventOut
from .security import require_role

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])


@router.get("/audit-events", response_model=list[AuditEventOut])
def list_audit_events(
    context: Context,
    db: Database,
    query: str = Query(default="", max_length=120),
    action_prefix: str = Query(default="", max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
):
    require_role(context, "viewer", "operator", "admin")
    statement = select(AuditEvent).where(AuditEvent.tenant_id == context.tenant_id)
    if action_prefix:
        statement = statement.where(AuditEvent.action.startswith(action_prefix))
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        statement = statement.where(
            or_(
                AuditEvent.action.ilike(pattern, escape="\\"),
                AuditEvent.resource_type.ilike(pattern, escape="\\"),
                AuditEvent.resource_id.ilike(pattern, escape="\\"),
                AuditEvent.actor_id.ilike(pattern, escape="\\"),
            )
        )
    rows = db.scalars(statement.order_by(AuditEvent.created_at.desc()).limit(limit))
    return [AuditEventOut.model_validate(row) for row in rows]
