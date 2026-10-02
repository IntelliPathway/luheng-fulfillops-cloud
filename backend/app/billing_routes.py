from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .dependencies import Context, Database
from .models import UsageEvent, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    start_date = date.fromisoformat(f"{period}-01")
    start = datetime.combine(start_date, datetime.min.time())
    if start_date.month == 12:
        end_date = date(start_date.year + 1, 1, 1)
    else:
        end_date = date(start_date.year, start_date.month + 1, 1)
    return start, datetime.combine(end_date, datetime.min.time())


@router.get("/usage-events")
def list_usage_events(
    context: Context,
    db: Database,
    period: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    require_role(context, "admin")
    start, end = _period_bounds(period)
    rows = db.scalars(
        select(UsageEvent)
        .where(
            UsageEvent.tenant_id == context.tenant_id,
            UsageEvent.occurred_at >= start,
            UsageEvent.occurred_at < end,
        )
        .order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "meter": row.meter,
            "quantity": row.quantity,
            "unit": row.unit,
            "amount_cents": row.amount_cents,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "metadata": row.metadata_json,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


@router.get("/invoice-preview")
def invoice_preview(
    context: Context,
    db: Database,
    period: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict:
    require_role(context, "viewer", "operator", "admin")
    start, end = _period_bounds(period)
    rows = db.execute(
        select(
            UsageEvent.meter,
            UsageEvent.unit,
            func.sum(UsageEvent.quantity),
            func.sum(UsageEvent.amount_cents),
            func.count(UsageEvent.id),
        )
        .where(
            UsageEvent.tenant_id == context.tenant_id,
            UsageEvent.occurred_at >= start,
            UsageEvent.occurred_at < end,
        )
        .group_by(UsageEvent.meter, UsageEvent.unit)
        .order_by(UsageEvent.meter)
    ).all()
    lines = [
        {"meter": meter, "unit": unit, "quantity": int(quantity), "amount_cents": int(amount), "event_count": count}
        for meter, unit, quantity, amount, count in rows
    ]
    return {
        "tenant_id": context.tenant_id,
        "period": period,
        "status": "preview",
        "currency": "CNY",
        "lines": lines,
        "subtotal_cents": sum(line["amount_cents"] for line in lines),
        "generated_at": utcnow(),
        "automatic_charge": False,
    }
