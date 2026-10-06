import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.case_validation import build_case_validation
from app.financial_ledger import sign_payment_webhook
from app.main import create_app
from app.models import AssetPackage, PaymentReceipt


def test_case_validation_never_calls_seed_data_real_and_is_tenant_scoped():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
        response = client.get("/api/v1/pilot/case-validation?case_id=C002", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "evidence_incomplete"
        assert not data["real_business_verified"]
        assert not data["enables_external_execution"]
        assert data["source_digest"] is None
        assert len(data["report_digest"]) == 64
        assert not next(c for c in data["checks"] if c["id"] == "source")["passed"]
        assert not next(c for c in data["checks"] if c["id"] == "environment")["passed"]
        again = client.get("/api/v1/pilot/case-validation?case_id=C002", headers=headers).json()
        assert again["report_digest"] == data["report_digest"]
        assert (
            client.get(
                "/api/v1/pilot/case-validation?case_id=C002", headers={**headers, "X-Tenant-ID": "TENANT_B"}
            ).status_code
            == 404
        )
        assert client.get("/api/v1/pilot/case-validation?case_id=C002").status_code == 401
        assert client.get("/api/v1/pilot/case-validation?case_id=missing", headers=headers).status_code == 404


def test_imported_case_keeps_source_digest_and_requires_policy_and_real_outcome():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        actor = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
        csv = "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status\nPKG_VALIDATE,测试脱敏资产,VERIFY001,1200000,2026-01-01,2027-12-31,COM_VALIDATE,1500,CONSENT-VERIFY001,待联系"
        created = client.post(
            "/api/v1/asset-imports/previews",
            headers=actor,
            json={"filename": "cases.csv", "csv_text": csv, "idempotency_key": "validate-import-case"},
        )
        assert created.status_code == 201, created.text
        batch = created.json()
        reviewed = client.post(
            f"/api/v1/asset-imports/{batch['id']}/commit",
            headers={**actor, "X-Actor-ID": "Terry"},
            json={"expected_version": 1, "review_note": "独立核对脱敏案件的金额和委托期限", "acknowledged": True},
        )
        assert reviewed.status_code == 200, reviewed.text
        report = client.get("/api/v1/pilot/case-validation?case_id=VERIFY001", headers=actor).json()
        checks = {c["id"]: c for c in report["checks"]}
        assert checks["source"]["passed"]
        assert checks["mandate"]["passed"]
        assert not checks["policy"]["passed"]
        assert not checks["outcome"]["passed"]
        assert report["source_digest"] == batch["source_digest"]
        assert report["confirmed_net_recovery_cents"] == 0
        assert report["ledger"] == []
        assert not report["real_business_verified"]
        assert "source_filename" not in report


@pytest.fixture()
def ledger_case(monkeypatch):
    # Explicit synthetic sandbox credentials; never a real provider or production deployment.
    secret = "sandbox-payment-signing-secret-v1"
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "case-validation-test-v1")
    monkeypatch.setenv("PAYMENT_SANDBOX_SECRET", secret)
    monkeypatch.setenv("ENABLE_PAYMENT_SANDBOX", "true")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        actor = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
        csv = "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status\nPKG_REPORT,脱敏回款测试,C901,1200000,2020-01-01,2099-12-31,COM_REPORT,1500,CONSENT-C901,待联系"
        preview = client.post(
            "/api/v1/asset-imports/previews",
            headers=actor,
            json={"filename": "cases.csv", "csv_text": csv, "idempotency_key": "report-ledger-import"},
        )
        assert preview.status_code == 201, preview.text
        batch = preview.json()
        committed = client.post(
            f"/api/v1/asset-imports/{batch['id']}/commit",
            headers={**actor, "X-Actor-ID": "Terry"},
            json={"expected_version": 1, "review_note": "独立核对脱敏回款案件", "acknowledged": True},
        )
        assert committed.status_code == 200, committed.text
        with client.app.state.Session() as db:
            package = db.scalar(select(AssetPackage).where(AssetPackage.package_id == "PKG_REPORT"))
            package.policy_status = "published"  # Fixture: isolate outcome checks from policy approval tests.
            db.commit()
        yield client, actor, secret


def post_report_payment(client, secret, event_id, *, refund=False):
    payload = {
        "event_id": event_id,
        "event_type": "refund" if refund else "payment",
        "amount_cents": 3000 if refund else 10000,
        "currency": "CNY",
        "occurred_at": "2026-10-06T07:00:00Z",
        "case_id": "C901",
    }
    if refund:
        payload["original_event_id"] = "REPORT-PAYMENT"
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    result = client.post(
        "/api/v1/webhooks/payments/TENANT_A/sandbox-amc",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-FulfillOps-Timestamp": timestamp,
            "X-FulfillOps-Signature": sign_payment_webhook(secret, timestamp, raw),
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["receipt"]["status"] == "matched"
    return result.json()["receipt"]


def test_matched_signed_payment_and_refund_pass_outcome_and_keep_external_review_boundary(ledger_case):
    client, actor, secret = ledger_case
    for event_id, refund, expected_net in [("REPORT-PAYMENT", False, 10000), ("REPORT-REFUND", True, 7000)]:
        post_report_payment(client, secret, event_id, refund=refund)
        response = client.get("/api/v1/pilot/case-validation?case_id=C901", headers=actor)
        assert response.status_code == 200, response.text
        data = response.json()
        assert next(c for c in data["checks"] if c["id"] == "outcome")["passed"]
        assert data["confirmed_net_recovery_cents"] == expected_net
        assert data["status"] == "evidence_incomplete"  # Development still blocks real acceptance.
        with client.app.state.Session() as db:
            # Test only the report's environment gate, without deploying or connecting production.
            report = build_case_validation(
                db,
                "TENANT_A",
                "C901",
                replace(client.app.state.startup, environment="production", seed_demo_data=False),
            )
        assert report["status"] == "ready_for_external_review"
        assert not report["real_business_verified"]
        assert not report["enables_external_execution"]
        assert report["source_authenticity"] == "requires_external_attestation"
    assert sorted(e["amount_cents"] for e in data["ledger"]) == [-3000, 10000]


@pytest.mark.parametrize(
    "invalid", ["accepted", "unmatched", "review_pending", "rejected", "unsigned", "bad_backlink", "missing_link"]
)
def test_invalid_receipt_evidence_does_not_pass_outcome(ledger_case, invalid):
    client, actor, secret = ledger_case
    receipt = post_report_payment(client, secret, "REPORT-PAYMENT")
    with client.app.state.Session() as db:
        row = db.get(PaymentReceipt, receipt["id"])
        if invalid == "unsigned":
            row.signature_verified = False
        elif invalid == "bad_backlink":
            row.recovery_entry_id = "sandbox-amc:other-entry"
        elif invalid == "missing_link":
            row.case_id = "C002"
        else:
            row.status = invalid
        db.commit()
    report = client.get("/api/v1/pilot/case-validation?case_id=C901", headers=actor).json()
    assert not next(c for c in report["checks"] if c["id"] == "outcome")["passed"]
    assert report["status"] == "evidence_incomplete"
