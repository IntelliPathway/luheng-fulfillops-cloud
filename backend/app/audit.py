from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditEvent
from .security import RequestContext


def audit(
    db: Session,
    context: RequestContext,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: dict,
) -> None:
    db.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
        )
    )
