from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.financial_ledger import sign_payment_webhook
from app.main import create_app
from app.models import (
    AuditEvent,
    CommissionLedgerEntry,
    ManagedSecret,
    PaymentReceipt,
    PaymentReconciliation,
    RecoveryLedgerEntry,
)

PAYMENT_SECRET = "sandbox-payment-signing-secret-v1"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "ledger-test-v1")
    monkeypatch.setenv("PAYMENT_SANDBOX_SECRET", PAYMENT_SECRET)
    monkeypatch.setenv("ENABLE_PAYMENT_SANDBOX", "true")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def auth_headers(tenant: str = "TENANT_A", role: str = "admin") -> dict[str, str]:
    actor = {"admin": "test-user", "operator": "test-operator", "viewer": "test-viewer"}[role]
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def signed_event(
    client: TestClient,
    payload: dict,
    *,
    tenant: str = "TENANT_A",
    provider: str = "sandbox-amc",
    secret: str = PAYMENT_SECRET,
    timestamp: int | None = None,
):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signed_at = str(timestamp or int(time.time()))
    signature = sign_payment_webhook(secret, signed_at, raw)
    return client.post(
        f"/api/v1/webhooks/payments/{tenant}/{provider}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-FulfillOps-Timestamp": signed_at,
            "X-FulfillOps-Signature": signature,
        },
    )


def payment_payload(event_id: str, amount_cents: int = 10_000, case_id: str = "C002") -> dict:
    return {
        "event_id": event_id,
        "event_type": "payment",
        "amount_cents": amount_cents,
        "currency": "CNY",
        "occurred_at": "2026-09-14T12:00:00Z",
        "case_id": case_id,
    }


def test_financial_overview_is_tenant_scoped_and_uses_integer_cents(client: TestClient) -> None:
    tenant_a = client.get("/api/v1/payments/overview", headers=auth_headers()).json()
    tenant_b = client.get("/api/v1/payments/overview", headers=auth_headers("TENANT_B", "viewer")).json()
    assert tenant_a["summary"] == {
        "confirmed_net_recovery_cents": 1_992_000,
        "commission_eligible_recovery_cents": 1_842_000,
        "accrued_commission_cents": 277_800,
        "settled_commission_cents": 0,
        "collected_commission_cents": 0,
        "unsettled_commission_cents": 277_800,
        "uncollected_settlement_cents": 0,
        "pending_receipt_count": 0,
    }
    assert tenant_a["webhook_ready"] is True
    assert tenant_a["webhook_provider"] == "sandbox-amc"
    assert tenant_b["summary"]["confirmed_net_recovery_cents"] == 80_000
    assert all(row["case_id"] != "C021" for row in tenant_a["recovery_ledger"])


def test_provider_event_ids_and_derived_entries_are_unique_within_each_tenant(client: TestClient) -> None:
    event_id = "SHARED-PROVIDER-EVENT-1"
    configured = client.put(
        "/api/v1/payments/webhook-configs/sandbox-amc",
        headers=auth_headers("TENANT_B"),
        json={"credential": PAYMENT_SECRET, "max_amount_cents": 100_000_000},
    )
    assert configured.status_code == 200, configured.text
    tenant_a = signed_event(client, payment_payload(event_id, 10_000, "C002"))
    tenant_b = signed_event(
        client,
        payment_payload(event_id, 20_000, "C021"),
        tenant="TENANT_B",
    )
    assert tenant_a.status_code == 200, tenant_a.text
    assert tenant_b.status_code == 200, tenant_b.text
    assert tenant_a.json()["receipt"]["status"] == "matched"
    assert tenant_b.json()["receipt"]["status"] == "matched"
    with client.app.state.Session() as db:
        recoveries = list(
            db.scalars(select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.entry_id == f"sandbox-amc:{event_id}"))
        )
        commissions = list(
            db.scalars(
                select(CommissionLedgerEntry).where(
                    CommissionLedgerEntry.source_recovery_entry_id == f"sandbox-amc:{event_id}"
                )
            )
        )
    assert {row.tenant_id for row in recoveries} == {"TENANT_A", "TENANT_B"}
    assert {row.tenant_id for row in commissions} == {"TENANT_A", "TENANT_B"}


def test_webhook_rejects_bad_or_expired_signatures_without_persisting_receipts(client: TestClient) -> None:
    payload = payment_payload("BAD-SIGNATURE-1")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    now = str(int(time.time()))
    invalid = client.post(
        "/api/v1/webhooks/payments/TENANT_A/sandbox-amc",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-FulfillOps-Timestamp": now,
            "X-FulfillOps-Signature": f"v1={'0' * 64}",
        },
    )
    assert invalid.status_code == 401
    assert "invalid_signature" in invalid.json()["detail"]
    expired = signed_event(client, payload, timestamp=int(time.time()) - 901)
    assert expired.status_code == 401
    assert "signature_expired" in expired.json()["detail"]
    with client.app.state.Session() as db:
        assert db.scalar(select(func.count(PaymentReceipt.id))) == 0


def test_signed_payment_is_atomic_idempotent_and_conflict_safe(client: TestClient) -> None:
    payload = payment_payload("PAY-ATOMIC-1", 101_600)
    accepted = signed_event(client, payload)
    assert accepted.status_code == 200, accepted.text
    receipt = accepted.json()["receipt"]
    assert accepted.json()["duplicate"] is False
    assert receipt["status"] == "matched"
    assert receipt["signature_verified"] is True
    assert len(receipt["payload_digest"]) == 64
    assert len(receipt["signature_digest"]) == 64
    assert PAYMENT_SECRET not in accepted.text

    repeated = signed_event(client, payload)
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True
    assert repeated.json()["receipt"]["duplicate_count"] == 1

    conflict = signed_event(client, payment_payload("PAY-ATOMIC-1", 101_601))
    assert conflict.status_code == 409
    assert "idempotency_conflict" in conflict.json()["detail"]

    overview = client.get("/api/v1/payments/overview", headers=auth_headers()).json()
    assert overview["summary"]["confirmed_net_recovery_cents"] == 2_093_600
    assert overview["summary"]["accrued_commission_cents"] == 293_040
    assert sum(row["entry_id"] == "sandbox-amc:PAY-ATOMIC-1" for row in overview["recovery_ledger"]) == 1


def test_unmatched_receipt_requires_scoped_maker_checker_reconciliation(client: TestClient) -> None:
    accepted = signed_event(client, payment_payload("PAY-UNMATCHED-1", case_id="C999"))
    assert accepted.status_code == 200
    receipt = accepted.json()["receipt"]
    assert receipt["status"] == "unmatched"
    assert receipt["failure_code"] == "case_unmatched"

    legacy_path = f"/api/v1/payments/receipts/{receipt['id']}/match"
    viewer = client.post(
        legacy_path,
        headers=auth_headers(role="viewer"),
        json={"case_id": "C002", "acknowledged": True},
    )
    assert viewer.status_code == 403
    assert (
        client.post(
            legacy_path,
            headers=auth_headers(),
            json={"case_id": "C002", "acknowledged": True},
        ).status_code
        == 409
    )

    candidates_path = f"/api/v1/payments/receipts/{receipt['id']}/candidates"
    candidates = client.get(candidates_path, headers=auth_headers(role="viewer"))
    assert candidates.status_code == 200
    c002 = next(row for row in candidates.json() if row["case_id"] == "C002")
    assert c002["has_signed_plan"] is True
    assert c002["score"] < 100

    proposal_path = f"/api/v1/payments/receipts/{receipt['id']}/reconciliations"
    cross_tenant = client.post(
        proposal_path,
        headers=auth_headers("TENANT_B", "operator"),
        json={"case_id": "C021", "reason": "已核对银行附言与合同编号", "acknowledged": True},
    )
    assert cross_tenant.status_code == 404
    missing_ack = client.post(
        proposal_path,
        headers=auth_headers(role="operator"),
        json={"case_id": "C002", "reason": "已核对银行附言与合同编号", "acknowledged": False},
    )
    assert missing_ack.status_code == 422
    proposed = client.post(
        proposal_path,
        headers=auth_headers(role="operator"),
        json={"case_id": "C002", "reason": "已核对银行附言与合同编号", "acknowledged": True},
    )
    assert proposed.status_code == 200, proposed.text
    review = proposed.json()
    assert review["status"] == "pending_review"
    assert review["proposed_by"] == "test-operator"
    assert review["proposed_case_id"] == "C002"
    assert len(review["evidence_digest"]) == 64
    repeated_proposal = client.post(
        proposal_path,
        headers=auth_headers(role="operator"),
        json={"case_id": "C002", "reason": "已核对银行附言与合同编号", "acknowledged": True},
    )
    assert repeated_proposal.status_code == 200
    assert repeated_proposal.json()["id"] == review["id"]
    assert repeated_proposal.json()["version"] == 1

    decision_path = f"/api/v1/payments/reconciliations/{review['id']}/decision"
    wrong_version = client.post(
        decision_path,
        headers=auth_headers(),
        json={"decision": "approve", "review_note": "复核凭证一致", "expected_version": 2, "acknowledged": True},
    )
    assert wrong_version.status_code == 409
    matched = client.post(
        decision_path,
        headers=auth_headers(),
        json={"decision": "approve", "review_note": "复核凭证一致", "expected_version": 1, "acknowledged": True},
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["status"] == "approved"
    assert matched.json()["reviewed_by"] == "test-user"
    assert matched.json()["recovery_entry_id"] == "sandbox-amc:PAY-UNMATCHED-1"
    overview = client.get("/api/v1/payments/overview", headers=auth_headers()).json()
    assert overview["summary"]["pending_receipt_count"] == 0
    assert overview["reconciliations"][0]["status"] == "approved"

    with client.app.state.Session() as db:
        stored = db.scalar(select(PaymentReconciliation).where(PaymentReconciliation.id == review["id"]))
        actions = set(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.resource_id == review["id"],
                )
            )
        )
        assert stored and stored.version == 2
        assert {"payment.reconciliation.proposed", "payment.reconciliation.approved"} <= actions


def test_reconciliation_rejects_self_review_and_keeps_money_out_of_ledger(client: TestClient) -> None:
    receipt = signed_event(client, payment_payload("PAY-REVIEW-SELF", case_id="C999")).json()["receipt"]
    proposal = client.post(
        f"/api/v1/payments/receipts/{receipt['id']}/reconciliations",
        headers=auth_headers(),
        json={"case_id": "C002", "reason": "管理员创建的人工匹配提案", "acknowledged": True},
    ).json()
    decision_path = f"/api/v1/payments/reconciliations/{proposal['id']}/decision"
    same_actor = client.post(
        decision_path,
        headers=auth_headers(),
        json={"decision": "approve", "review_note": "尝试自我复核", "expected_version": 1, "acknowledged": True},
    )
    assert same_actor.status_code == 409
    assert "maker_checker_conflict" in same_actor.json()["detail"]

    with client.app.state.Session() as db:
        assert (
            db.scalar(select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.entry_id == "sandbox-amc:PAY-REVIEW-SELF"))
            is None
        )


def test_rejected_reconciliation_stays_out_of_metrics_and_can_be_resubmitted(client: TestClient) -> None:
    receipt = signed_event(client, payment_payload("PAY-REVIEW-REJECT", case_id="C999")).json()["receipt"]
    proposal_path = f"/api/v1/payments/receipts/{receipt['id']}/reconciliations"
    proposal = client.post(
        proposal_path,
        headers=auth_headers(role="operator"),
        json={"case_id": "C002", "reason": "首次核对付款附言后提交复核", "acknowledged": True},
    ).json()
    rejected = client.post(
        f"/api/v1/payments/reconciliations/{proposal['id']}/decision",
        headers=auth_headers(),
        json={"decision": "reject", "review_note": "付款附言证据不足", "expected_version": 1, "acknowledged": True},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["version"] == 2
    overview = client.get("/api/v1/payments/overview", headers=auth_headers()).json()
    assert overview["summary"]["pending_receipt_count"] == 0
    assert not any(row["entry_id"] == "sandbox-amc:PAY-REVIEW-REJECT" for row in overview["recovery_ledger"])

    resubmitted = client.post(
        proposal_path,
        headers=auth_headers(role="operator"),
        json={"case_id": "C002", "reason": "补充银行流水编号后重新提交复核", "acknowledged": True},
    )
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == "pending_review"
    assert resubmitted.json()["version"] == 3


def test_refund_uses_original_rate_and_cannot_exceed_original_payment(client: TestClient) -> None:
    assert signed_event(client, payment_payload("PAY-REFUND-ORIGINAL", 10_000)).json()["receipt"]["status"] == "matched"
    refund = {
        "event_id": "REFUND-1",
        "event_type": "refund",
        "amount_cents": 3_000,
        "currency": "CNY",
        "occurred_at": "2026-09-14T13:00:00Z",
        "case_id": "C002",
        "original_event_id": "PAY-REFUND-ORIGINAL",
    }
    reversed_receipt = signed_event(client, refund)
    assert reversed_receipt.status_code == 200
    assert reversed_receipt.json()["receipt"]["status"] == "matched"
    overview = client.get("/api/v1/payments/overview", headers=auth_headers()).json()
    reversal = next(row for row in overview["recovery_ledger"] if row["entry_id"] == "sandbox-amc:REFUND-1")
    assert reversal["amount_cents"] == -3_000
    assert reversal["commission_cents"] == -450
    assert reversal["reason"] == "REFUND_ORIGINAL_RATE"

    excessive = {**refund, "event_id": "REFUND-2", "amount_cents": 8_000}
    review = signed_event(client, excessive).json()["receipt"]
    assert review["status"] == "review_required"
    assert review["failure_code"] == "refund_exceeds_payment"
    with client.app.state.Session() as db:
        assert (
            db.scalar(
                select(func.count(RecoveryLedgerEntry.id)).where(RecoveryLedgerEntry.entry_id == "sandbox-amc:REFUND-2")
            )
            == 0
        )


def test_commission_settlement_and_collection_are_bounded_and_idempotent(client: TestClient) -> None:
    settlement = {
        "event_type": "settlement",
        "amount_cents": 100_000,
        "reference": "AMC-SETTLEMENT-202609",
        "idempotency_key": "settlement-202609-v1",
        "occurred_at": "2026-09-14T14:00:00Z",
        "acknowledged": True,
    }
    assert (
        client.post("/api/v1/commissions/events", headers=auth_headers(role="viewer"), json=settlement).status_code
        == 403
    )
    created = client.post("/api/v1/commissions/events", headers=auth_headers(), json=settlement)
    assert created.status_code == 200, created.text
    assert created.json()["duplicate"] is False
    repeated = client.post("/api/v1/commissions/events", headers=auth_headers(), json=settlement)
    assert repeated.json()["duplicate"] is True
    conflict = client.post(
        "/api/v1/commissions/events",
        headers=auth_headers(),
        json={**settlement, "amount_cents": 100_001},
    )
    assert conflict.status_code == 409

    excessive_collection = client.post(
        "/api/v1/commissions/events",
        headers=auth_headers(),
        json={
            **settlement,
            "event_type": "collection",
            "amount_cents": 100_001,
            "reference": "BANK-COLLECTION-EXCESS",
            "idempotency_key": "collection-excess-v1",
        },
    )
    assert excessive_collection.status_code == 409
    collected = client.post(
        "/api/v1/commissions/events",
        headers=auth_headers(),
        json={
            **settlement,
            "event_type": "collection",
            "amount_cents": 60_000,
            "reference": "BANK-COLLECTION-202609",
            "idempotency_key": "collection-202609-v1",
        },
    )
    assert collected.status_code == 200
    summary = client.get("/api/v1/payments/overview", headers=auth_headers()).json()["summary"]
    assert summary["settled_commission_cents"] == 100_000
    assert summary["collected_commission_cents"] == 60_000
    assert summary["uncollected_settlement_cents"] == 40_000


def test_sandbox_endpoint_uses_same_signature_and_ledger_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = {"case_id": "C002", "amount_cents": 101_600, "idempotency_key": "ui-demo-payment-1"}
    created = client.post("/api/v1/payments/sandbox-receipts", headers=auth_headers(role="operator"), json=request)
    assert created.status_code == 200, created.text
    assert created.json()["receipt"]["provider_event_id"] == "SBX-ui-demo-payment-1"
    assert created.json()["receipt"]["status"] == "matched"
    repeated = client.post("/api/v1/payments/sandbox-receipts", headers=auth_headers(role="operator"), json=request)
    assert repeated.json()["duplicate"] is True
    monkeypatch.setenv("ENABLE_PAYMENT_SANDBOX", "false")
    denied = client.post(
        "/api/v1/payments/sandbox-receipts",
        headers=auth_headers(role="operator"),
        json={**request, "idempotency_key": "ui-demo-payment-2"},
    )
    assert denied.status_code == 409


def test_webhook_secret_is_encrypted_and_agent_metrics_read_the_ledger(client: TestClient) -> None:
    credential = "rotated-payment-signing-secret-9876"
    saved = client.put(
        "/api/v1/payments/webhook-configs/acquirer-a",
        headers=auth_headers(),
        json={"credential": credential, "max_amount_cents": 5_000_000},
    )
    assert saved.status_code == 200
    assert saved.json()["credential_mask"] == "••••9876"
    assert credential not in saved.text
    with client.app.state.Session() as db:
        secret = db.scalar(select(ManagedSecret).where(ManagedSecret.service_type == "payment-acquirer-a"))
        assert secret is not None
        assert credential not in secret.ciphertext

    signed_event(client, payment_payload("PAY-AGENT-METRIC", 10_000))
    session = client.post(
        "/api/v1/agents/sessions",
        headers=auth_headers(role="viewer"),
        json={"scope_type": "global", "title": "账簿指标"},
    ).json()
    queued = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=auth_headers(role="viewer"),
        json={"content": "查询回款和实收佣金", "idempotency_key": "ledger-chatbi-v1"},
    ).json()
    job = client.get(f"/api/v1/jobs/{queued['id']}", headers=auth_headers(role="viewer")).json()
    assert "¥20,020" in job["result"]["answer"]["body"]
    assert {source["entity_type"] for source in job["result"]["answer"]["sources"]} == {
        "recovery_ledger",
        "commission_ledger",
    }
    with client.app.state.Session() as db:
        assert db.scalar(select(func.count(CommissionLedgerEntry.id))) > 0
