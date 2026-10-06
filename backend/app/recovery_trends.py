"""Read-only daily ledger trends, grouped by provider event time in UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from .models import RecoveryLedgerEntry


def build_recovery_trend(db, tenant_id: str, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    today = now.astimezone(UTC).date()
    start = today - timedelta(days=days - 1)
    begin = datetime.combine(start, datetime.min.time())
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    day = func.date(RecoveryLedgerEntry.booked_at)
    rows = db.execute(
        select(
            day,
            func.sum(RecoveryLedgerEntry.amount_cents),
            func.sum(RecoveryLedgerEntry.commission_cents),
            func.count(RecoveryLedgerEntry.id),
            func.sum(case((RecoveryLedgerEntry.event_type == "PAYMENT", RecoveryLedgerEntry.amount_cents), else_=0)),
            -func.sum(case((RecoveryLedgerEntry.event_type == "REFUND", RecoveryLedgerEntry.amount_cents), else_=0)),
        )
        .where(
            RecoveryLedgerEntry.tenant_id == tenant_id,
            RecoveryLedgerEntry.booked_at >= begin,
            RecoveryLedgerEntry.booked_at < end,
        )
        .group_by(day)
        .order_by(day)
    ).all()
    values = {str(row[0]): row for row in rows}
    points = []
    for i in range(days):
        date = (start + timedelta(days=i)).isoformat()
        row = values.get(date)
        points.append(
            {
                "date": date,
                "net_recovery_cents": int(row[1]) if row else 0,
                "accrued_commission_cents": int(row[2]) if row else 0,
                "ledger_entry_count": int(row[3]) if row else 0,
                "payment_total_cents": int(row[4]) if row else 0,
                "refund_total_cents": int(row[5]) if row else 0,
            }
        )
    return {
        "tenant_id": tenant_id,
        "days": days,
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": today.isoformat(),
        "source": "immutable_recovery_ledger",
        "date_basis": "provider_event_time",
        "generated_at": now,
        "net_recovery_cents": sum(p["net_recovery_cents"] for p in points),
        "payment_total_cents": sum(p["payment_total_cents"] for p in points),
        "refund_total_cents": sum(p["refund_total_cents"] for p in points),
        "accrued_commission_cents": sum(p["accrued_commission_cents"] for p in points),
        "ledger_entry_count": sum(p["ledger_entry_count"] for p in points),
        "points": points,
    }
