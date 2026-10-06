from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_case_validation import ledger_case as ledger_case
from test_case_validation import post_report_payment

from app.main import create_app
from app.models import RecoveryLedgerEntry
from app.recovery_trends import build_recovery_trend


def test_trend_auth_tenant_daily_zero_and_range_boundary():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        path = "/api/v1/pilot/recovery-trend"
        assert client.get(path).status_code == 401
        for tenant in ["TENANT_A", "TENANT_B"]:
            response = client.get(
                path, params={"days": 7}, headers={"X-Tenant-ID": tenant, "X-Actor-ID": "test-viewer"}
            )
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            report = response.json()
            assert report["tenant_id"] == tenant
            assert report["timezone"] == "UTC"
            assert len(report["points"]) == 7
            assert report["points"][0]["date"] == report["start_date"]
            assert report["points"][-1]["date"] == report["end_date"]
        for days in [0, 91, "bad"]:
            assert (
                client.get(
                    path, params={"days": days}, headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
                ).status_code
                == 422
            )


def test_trend_period_totals_refund_negative_utc_boundary_and_tenant_scope(ledger_case):
    client, actor, secret = ledger_case
    post_report_payment(client, secret, "REPORT-PAYMENT")
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    post_report_payment(client, secret, "REPORT-PAYMENT")  # Replay must not add a trend point or amount.
    with client.app.state.Session() as db:
        rows = list(db.scalars(select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.case_id == "C901")))
        for row in rows:
            row.booked_at = datetime(2026, 10, 5, 23, 59, 59) if row.event_type == "PAYMENT" else datetime(2026, 10, 6)
        db.commit()
        now = datetime(2026, 10, 6, 8, tzinfo=UTC)
        today = build_recovery_trend(db, "TENANT_A", 1, now)
        assert today["net_recovery_cents"] == -3000
        assert today["payment_total_cents"] == 0
        assert today["refund_total_cents"] == 3000
        assert today["ledger_entry_count"] == 1
        week = build_recovery_trend(db, "TENANT_A", 7, now)
        assert week["net_recovery_cents"] == 7000
        assert week["payment_total_cents"] == 10000
        assert week["refund_total_cents"] == 3000
        assert week["ledger_entry_count"] == 2
        assert week["net_recovery_cents"] == sum(p["net_recovery_cents"] for p in week["points"])
        assert all(p["ledger_entry_count"] == 0 for p in week["points"][:-2])
        other = build_recovery_trend(db, "TENANT_B", 7, now)
        assert other["ledger_entry_count"] == 0
        assert other["net_recovery_cents"] == 0
        # The upper bound excludes tomorrow's provider event.
        rows[0].booked_at = datetime(2026, 10, 7)
        db.commit()
        assert build_recovery_trend(db, "TENANT_A", 7, now)["ledger_entry_count"] == 1
