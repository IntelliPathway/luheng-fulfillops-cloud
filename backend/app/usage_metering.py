from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UsageEvent, utcnow

METER_PRICES = {
    "agent_run": {"unit": "run", "unit_price_cents": 5},
    "model_token": {"unit": "token", "unit_price_cents": 0},
    "channel_task": {"unit": "task", "unit_price_cents": 2},
}


def record_usage_event(
    db: Session,
    *,
    tenant_id: str,
    meter: str,
    quantity: int,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    amount_cents: int | None = None,
    metadata: dict | None = None,
    occurred_at: datetime | None = None,
) -> UsageEvent:
    existing = db.scalar(
        select(UsageEvent).where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    if meter not in METER_PRICES:
        raise ValueError(f"未知用量计量项：{meter}")
    if quantity <= 0:
        raise ValueError("用量数量必须大于零")
    price = METER_PRICES[meter]
    event = UsageEvent(
        tenant_id=tenant_id,
        meter=meter,
        quantity=quantity,
        unit=price["unit"],
        unit_price_cents=price["unit_price_cents"],
        amount_cents=amount_cents if amount_cents is not None else quantity * price["unit_price_cents"],
        source_type=source_type,
        source_id=source_id,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
        occurred_at=occurred_at or utcnow(),
    )
    db.add(event)
    db.flush()
    return event
