"""Read-only daily ledger trends, grouped by provider event time in UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, case, func, select

from .models import PaymentReceipt, RecoveryLedgerEntry


def build_recovery_day(db, tenant_id: str, day: date, page: int, page_size: int) -> dict:
    """Paginated ledger provenance, never an external business attestation."""
    begin = datetime.combine(day, datetime.min.time())
    end = begin + timedelta(days=1)
    scope = (
        RecoveryLedgerEntry.tenant_id == tenant_id,
        RecoveryLedgerEntry.booked_at >= begin,
        RecoveryLedgerEntry.booked_at < end,
    )
    total, net, commission = db.execute(
        select(
            func.count(RecoveryLedgerEntry.id),
            func.coalesce(func.sum(RecoveryLedgerEntry.amount_cents), 0),
            func.coalesce(func.sum(RecoveryLedgerEntry.commission_cents), 0),
        ).where(*scope)
    ).one()
    rows = db.execute(
        select(RecoveryLedgerEntry, PaymentReceipt)
        .outerjoin(
            PaymentReceipt,
            and_(PaymentReceipt.id == RecoveryLedgerEntry.receipt_id, PaymentReceipt.tenant_id == tenant_id),
        )
        .where(*scope)
        .order_by(RecoveryLedgerEntry.booked_at, RecoveryLedgerEntry.entry_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for entry, receipt in rows:
        linked = bool(
            receipt
            and receipt.signature_verified
            and receipt.status == "matched"
            and receipt.case_id == entry.case_id
            and receipt.recovery_entry_id == entry.entry_id
        )
        items.append(
            {
                "entry_id": entry.entry_id,
                "case_id": entry.case_id,
                "event_type": entry.event_type,
                "amount_cents": entry.amount_cents,
                "commission_cents": entry.commission_cents,
                "occurred_at": entry.booked_at,
                "receipt_id": receipt.id if receipt else None,
                "evidence_status": "linked" if linked else "incomplete",
            }
        )
    return {
        "tenant_id": tenant_id,
        "date": day.isoformat(),
        "timezone": "UTC",
        "source": "immutable_recovery_ledger",
        "date_basis": "provider_event_time",
        "page": page,
        "page_size": page_size,
        "total": total,
        "net_recovery_cents": int(net),
        "accrued_commission_cents": int(commission),
        "items": items,
        "real_business_verified": False,
        "enables_external_execution": False,
    }


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
