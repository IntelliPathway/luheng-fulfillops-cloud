from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import ModelReplayRun, PaymentReceipt

PENDING_PAYMENT_STATUSES = {"unmatched", "review_required", "review_pending", "rejected"}


def provider_scorecard(db: Session, tenant_id: str) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_limit = float(os.getenv("MODEL_DAILY_COST_USD_PER_TENANT", "1.0"))
    model = db.execute(
        select(
            func.count(ModelReplayRun.id),
            func.coalesce(func.sum(ModelReplayRun.estimated_cost_usd), 0),
            func.coalesce(func.sum(ModelReplayRun.input_tokens), 0),
            func.coalesce(func.sum(ModelReplayRun.output_tokens), 0),
            func.coalesce(
                func.sum(case((ModelReplayRun.status == "failed", 1), else_=0)),
                0,
            ),
        ).where(ModelReplayRun.tenant_id == tenant_id, ModelReplayRun.created_at >= day_start)
    ).one()
    payment = db.execute(
        select(
            func.count(PaymentReceipt.id),
            func.coalesce(func.sum(PaymentReceipt.amount_cents), 0),
            func.min(PaymentReceipt.received_at),
        ).where(
            PaymentReceipt.tenant_id == tenant_id,
            PaymentReceipt.status.in_(PENDING_PAYMENT_STATUSES),
        )
    ).one()
    exceptions = list(
        db.execute(
            select(
                PaymentReceipt.id,
                PaymentReceipt.provider,
                PaymentReceipt.event_type,
                PaymentReceipt.amount_cents,
                PaymentReceipt.status,
                PaymentReceipt.failure_code,
                PaymentReceipt.received_at,
            )
            .where(
                PaymentReceipt.tenant_id == tenant_id,
                PaymentReceipt.status.in_(PENDING_PAYMENT_STATUSES),
            )
            .order_by(PaymentReceipt.received_at)
            .limit(50)
        ).mappings()
    )
    spent = round(float(model[1]), 6)
    oldest = payment[2]
    return {
        "tenant_id": tenant_id,
        "generated_at": now,
        "model_budget": {
            "daily_limit_usd": daily_limit,
            "spent_usd": spent,
            "remaining_usd": round(max(0, daily_limit - spent), 6),
            "utilization_percent": round(spent / daily_limit * 100, 2) if daily_limit > 0 else 100,
            "run_count": int(model[0]),
            "failed_run_count": int(model[4]),
            "input_tokens": int(model[2]),
            "output_tokens": int(model[3]),
        },
        "payment_exceptions": {
            "count": int(payment[0]),
            "amount_cents": int(payment[1]),
            "oldest_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0,
            "items": [dict(row) for row in exceptions],
        },
    }
