from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_case_validation import ledger_case as ledger_case
from test_case_validation import post_report_payment

from app.main import create_app
from app.models import PaymentReceipt, RecoveryLedgerEntry
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


def test_daily_detail_signed_receipts_pagination_totals_and_scope(ledger_case):
    client, actor, secret = ledger_case
    post_report_payment(client, secret, "REPORT-PAYMENT")
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    post_report_payment(client, secret, "REPORT-PAYMENT")
    path = "/api/v1/pilot/recovery-day"
    params = {"day": "2026-10-06", "page_size": 1}
    assert client.get(path, params=params).status_code == 401
    pages = []
    for page in [1, 2]:
        response = client.get(path, params={**params, "page": page}, headers=actor)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["total"] == 2
        assert data["net_recovery_cents"] == 7000  # All-day total, not current page.
        assert data["accrued_commission_cents"] == 1050
        assert not data["real_business_verified"] and not data["enables_external_execution"]
        assert len(data["items"]) == 1
        assert data["items"][0]["evidence_status"] == "linked"
        assert "payload_digest" not in data["items"][0]
        pages.extend(data["items"])
    assert {row["amount_cents"] for row in pages} == {10000, -3000}
    assert len({row["entry_id"] for row in pages}) == 2
    other = client.get(path, params=params, headers={**actor, "X-Tenant-ID": "TENANT_B"}).json()
    assert other["items"] == [] and other["total"] == 0 and other["net_recovery_cents"] == 0
    assert client.get(path, params={**params, "page": 3}, headers=actor).json()["items"] == []
    for bad in [{"day": "invalid"}, {"day": "9999-12-31"}, {**params, "page": 0}, {**params, "page_size": 101}]:
        assert client.get(path, params=bad, headers=actor).status_code == 422


def test_daily_detail_utc_boundary_and_broken_receipt_provenance(ledger_case):
    client, actor, secret = ledger_case
    post_report_payment(client, secret, "REPORT-PAYMENT")
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    with client.app.state.Session() as db:
        entries = list(db.scalars(select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.case_id == "C901")))
        payment = next(e for e in entries if e.event_type == "PAYMENT")
        refund = next(e for e in entries if e.event_type == "REFUND")
        payment.booked_at = datetime(2026, 10, 5, 23, 59, 59)
        refund.booked_at = datetime(2026, 10, 6)
        receipt = db.get(PaymentReceipt, refund.receipt_id)
        db.commit()
        path = "/api/v1/pilot/recovery-day?day=2026-10-06"
        report = client.get(path, headers=actor).json()
        assert report["total"] == 1 and report["net_recovery_cents"] == -3000
        for field, wrong, original in [
            ("signature_verified", False, True),
            ("status", "unmatched", "matched"),
            ("case_id", "C002", "C901"),
            ("recovery_entry_id", "wrong-backlink", refund.entry_id),
            ("tenant_id", "TENANT_B", "TENANT_A"),
        ]:
            setattr(receipt, field, wrong)
            db.commit()
            row = client.get(path, headers=actor).json()["items"][0]
            assert row["evidence_status"] == "incomplete"
            if field == "tenant_id":
                assert row["receipt_id"] is None  # Never leak another tenant's receipt.
            setattr(receipt, field, original)
            db.commit()
        refund.receipt_id = None
        db.commit()
        assert client.get(path, headers=actor).json()["items"][0]["evidence_status"] == "incomplete"
        empty = client.get("/api/v1/pilot/recovery-day?day=2026-10-07", headers=actor).json()
        assert empty["items"] == [] and empty["net_recovery_cents"] == 0


def test_daily_comparison_detects_offsetting_differences_and_checks_all_pages(ledger_case):
    client, actor, secret = ledger_case
    post_report_payment(client, secret, "REPORT-PAYMENT")
    zero_refund = client.get(
        "/api/v1/pilot/recovery-day",
        headers=actor,
        params={"day": "2026-10-06", "expected_payment_cents": 10000, "expected_refund_cents": 0},
    ).json()
    assert zero_refund["external_comparison"]["status"] == "matched"
    assert zero_refund["refund_total_cents"] == 0
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    path = "/api/v1/pilot/recovery-day"
    params = {"day": "2026-10-06", "page_size": 1, "expected_payment_cents": 10000, "expected_refund_cents": 3000}
    for page in [1, 2]:
        report = client.get(path, params={**params, "page": page}, headers=actor).json()
        assert report["payment_total_cents"] == 10000 and report["refund_total_cents"] == 3000
        assert report["linked_receipt_count"] == 2
        comparison = report["external_comparison"]
        assert comparison["status"] == "matched"
        assert comparison["payment_difference_cents"] == comparison["refund_difference_cents"] == 0
        assert not comparison["externally_attested"] and not report["real_business_verified"]
    # Net is still 7000, but both individual totals differ: never call this matched.
    mismatch = client.get(
        path, params={**params, "expected_payment_cents": 11000, "expected_refund_cents": 4000}, headers=actor
    ).json()
    assert mismatch["external_comparison"]["status"] == "mismatch"
    assert mismatch["external_comparison"]["payment_difference_cents"] == -1000
    assert mismatch["external_comparison"]["refund_difference_cents"] == -1000
    with client.app.state.Session() as db:
        # Break only the second page: first-page evidence must not authorize comparison.
        entry = db.scalar(
            select(RecoveryLedgerEntry).where(
                RecoveryLedgerEntry.event_type == "REFUND", RecoveryLedgerEntry.case_id == "C901"
            )
        )
        db.get(PaymentReceipt, entry.receipt_id).signature_verified = False
        db.commit()
        unavailable = client.get(path, params=params, headers=actor).json()
        assert unavailable["external_comparison"]["status"] == "unavailable"
        assert unavailable["external_comparison"]["payment_difference_cents"] is None
        assert unavailable["external_comparison"]["refund_difference_cents"] is None
    other = client.get(path, params=params, headers={**actor, "X-Tenant-ID": "TENANT_B"}).json()
    assert other["external_comparison"]["status"] == "unavailable"
    assert other["payment_total_cents"] == other["refund_total_cents"] == 0
    for bad in [
        {"day": params["day"], "expected_payment_cents": 0},
        {**params, "expected_payment_cents": -1},
        {**params, "expected_refund_cents": "1.5"},
        {**params, "expected_payment_cents": 9007199254740992},
    ]:
        assert client.get(path, params=bad, headers=actor).status_code == 422
    for field in ["expected_payment_cents", "expected_refund_cents"]:
        for invalid in ["1.0", "0.0", "1e2", "+1", " 1 ", "01", "١", "１", ""]:
            assert client.get(path, params={**params, field: invalid}, headers=actor).status_code == 422
    assert (
        client.get(path, params={**params, "expected_payment_cents": "9007199254740991"}, headers=actor).status_code
        == 200
    )
    empty = client.get(
        path,
        params={**params, "day": "2026-10-07", "expected_payment_cents": 0, "expected_refund_cents": 0},
        headers=actor,
    ).json()
    assert empty["external_comparison"]["status"] == "unavailable"
