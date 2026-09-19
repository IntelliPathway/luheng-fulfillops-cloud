from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from app.financial_ledger import sign_payment_webhook
from app.main import create_app
from app.migrations import run_sqlite_compatibility_migrations
from app.models import (
    AuditEvent,
    CaseFinancialProfile,
    CaseRecord,
    RecoveryLedgerEntry,
    RepaymentAllocation,
    RepaymentInstallment,
    RepaymentPlan,
)

PAYMENT_SECRET = "repayment-plan-signing-secret-v1"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "plan-test-v1")
    monkeypatch.setenv("PAYMENT_SANDBOX_SECRET", PAYMENT_SECRET)
    monkeypatch.setenv("ENABLE_PAYMENT_SANDBOX", "true")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def headers(actor: str = "test-user", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def plan_payload(plan_id: str = "PLAN-C008-NEW") -> dict:
    return {
        "plan_id": plan_id,
        "total_cents": 1_200_000,
        "down_payment_cents": 240_000,
        "installments": [
            {"installment_no": 1, "due_date": "2026-09-16", "due_cents": 240_000},
            {"installment_no": 2, "due_date": "2026-10-16", "due_cents": 480_000},
            {"installment_no": 3, "due_date": "2026-11-16", "due_cents": 480_000},
        ],
        "agreement_reference": "AGREEMENT-C008-001",
        "agreement_digest": "a" * 64,
        "signed_at": "2026-09-15T00:00:00Z",
        "proposal_reason": "已取得外部签署回执，条款满足当前资产包授权策略",
        "acknowledged": True,
    }


def propose(client: TestClient, case_id: str = "C008", actor: str = "test-user", plan_id: str = "PLAN-C008-NEW"):
    return client.post(
        f"/api/v1/cases/{case_id}/repayment-plans",
        headers=headers(actor),
        json=plan_payload(plan_id),
    )


def decide(client: TestClient, row_id: str, version: int, actor: str = "Terry", decision: str = "approve"):
    return client.post(
        f"/api/v1/repayment-plans/{row_id}/decision",
        headers=headers(actor),
        json={
            "decision": decision,
            "review_note": "已独立核验签署摘要、方案金额、期次与当前授权策略",
            "expected_version": version,
            "acknowledged": True,
        },
    )


def signed_event(client: TestClient, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = sign_payment_webhook(PAYMENT_SECRET, timestamp, raw)
    return client.post(
        "/api/v1/webhooks/payments/TENANT_A/sandbox-amc",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-FulfillOps-Timestamp": timestamp,
            "X-FulfillOps-Signature": signature,
        },
    )


def test_existing_sqlite_financial_tables_get_additive_plan_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'v11.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE asset_packages (id varchar(40) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE case_financial_profiles (id varchar(40) PRIMARY KEY)")

    applied = run_sqlite_compatibility_migrations(engine)
    inspector = inspect(engine)
    assert set(applied) == {
        "min_settlement_bps",
        "max_installments",
        "min_down_payment_bps",
        "source_import_batch_id",
        "created_at",
        "claim_balance_cents",
        "principal_cents",
        "interest_cents",
        "fee_cents",
        "first_overdue_date",
        "last_contact_at",
    }
    assert {column["name"] for column in inspector.get_columns("asset_packages")} >= {
        "min_settlement_bps",
        "max_installments",
        "min_down_payment_bps",
    }
    assert {column["name"] for column in inspector.get_columns("case_financial_profiles")} >= {
        "claim_balance_cents",
        "principal_cents",
        "interest_cents",
        "fee_cents",
        "first_overdue_date",
        "last_contact_at",
    }
    assert run_sqlite_compatibility_migrations(engine) == []
    engine.dispose()


def test_seeded_plan_overview_is_tenant_scoped_and_ledger_allocated(client: TestClient) -> None:
    tenant_a = client.get("/api/v1/repayment-plans/overview", headers=headers(actor="test-viewer")).json()
    tenant_b = client.get(
        "/api/v1/repayment-plans/overview",
        headers=headers(actor="test-viewer", tenant="TENANT_B"),
    ).json()
    assert len(tenant_a["plans"]) == 6
    assert tenant_a["pending_review_count"] == 1
    assert tenant_b["plans"] == []
    plan = next(item for item in tenant_a["plans"] if item["plan_id"] == "PLAN002")
    assert plan["paid_cents"] == 352_000
    assert plan["remaining_cents"] == 908_000
    assert plan["installments"][0]["status"] == "paid"
    assert plan["installments"][1]["paid_cents"] == 100_000
    assert plan["installments"][1]["remaining_cents"] == 101_600
    assert len(plan["agreement_digest"]) == 64
    assert PAYMENT_SECRET not in json.dumps(tenant_a)


def test_proposal_is_idempotent_and_requires_independent_current_review(client: TestClient) -> None:
    response = propose(client)
    assert response.status_code == 201, response.text
    proposed = response.json()
    assert proposed["status"] == "pending_review"
    assert proposed["version"] == 1
    assert proposed["paid_cents"] == 0
    repeated = propose(client)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == proposed["id"]

    self_review = decide(client, proposed["id"], 1, actor="test-user")
    assert self_review.status_code == 409
    assert "maker_checker_conflict" in self_review.json()["detail"]
    stale = decide(client, proposed["id"], 2)
    assert stale.status_code == 409
    assert "plan_version_conflict" in stale.json()["detail"]
    approved = decide(client, proposed["id"], 1)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"
    assert approved.json()["version"] == 2

    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C008"))
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == "TENANT_A",
                CaseFinancialProfile.case_id == "C008",
            )
        )
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == "TENANT_A",
                    AuditEvent.resource_id == proposed["id"],
                )
            )
        )
        assert case is not None and case.has_signed_plan is True and case.status == "履约中"
        assert profile is not None and profile.signed_plan_last_due.isoformat() == "2026-11-16"
        assert [event.action for event in events] == ["repayment_plan.proposed", "repayment_plan.approve"]
        assert all("已独立核验" not in str(event.detail) for event in events)


def test_policy_and_protection_boundaries_fail_closed(client: TestClient) -> None:
    protected = propose(client, case_id="C010", plan_id="PLAN-C010-DENIED")
    assert protected.status_code == 409
    assert "case_protected" in protected.json()["detail"]

    below_policy = plan_payload("PLAN-C008-BELOW")
    below_policy["total_cents"] = 1_000_000
    below_policy["installments"][-1]["due_cents"] = 280_000
    denied = client.post("/api/v1/cases/C008/repayment-plans", headers=headers(), json=below_policy)
    assert denied.status_code == 422
    assert "settlement_below_policy" in denied.json()["detail"]

    mismatch = plan_payload("PLAN-C008-MISMATCH")
    mismatch["installments"][-1]["due_cents"] = 479_999
    denied = client.post("/api/v1/cases/C008/repayment-plans", headers=headers(), json=mismatch)
    assert denied.status_code == 422
    assert "schedule_total_mismatch" in denied.json()["detail"]

    viewer = client.post("/api/v1/cases/C008/repayment-plans", headers=headers("test-viewer"), json=plan_payload())
    assert viewer.status_code == 403


def test_signed_payment_allocates_oldest_due_and_refund_reverses_latest_allocation(client: TestClient) -> None:
    proposed = propose(client).json()
    approved = decide(client, proposed["id"], proposed["version"])
    assert approved.status_code == 200
    payment = signed_event(
        client,
        {
            "event_id": "PLAN-ALLOCATION-PAYMENT-1",
            "event_type": "payment",
            "amount_cents": 300_000,
            "currency": "CNY",
            "occurred_at": "2026-09-15T01:00:00Z",
            "case_id": "C008",
        },
    )
    assert payment.status_code == 200, payment.text
    overview = client.get("/api/v1/repayment-plans/overview", headers=headers()).json()
    plan = next(item for item in overview["plans"] if item["plan_id"] == "PLAN-C008-NEW")
    assert [row["paid_cents"] for row in plan["installments"]] == [240_000, 60_000, 0]

    refund = signed_event(
        client,
        {
            "event_id": "PLAN-ALLOCATION-REFUND-1",
            "event_type": "refund",
            "amount_cents": 100_000,
            "currency": "CNY",
            "occurred_at": "2026-09-15T01:05:00Z",
            "case_id": "C008",
            "original_event_id": "PLAN-ALLOCATION-PAYMENT-1",
        },
    )
    assert refund.status_code == 200, refund.text
    overview = client.get("/api/v1/repayment-plans/overview", headers=headers()).json()
    plan = next(item for item in overview["plans"] if item["plan_id"] == "PLAN-C008-NEW")
    assert [row["paid_cents"] for row in plan["installments"]] == [200_000, 0, 0]
    with client.app.state.Session() as db:
        payment_entry = db.scalar(
            select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.entry_id == "sandbox-amc:PLAN-ALLOCATION-PAYMENT-1")
        )
        refund_entry = db.scalar(
            select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.entry_id == "sandbox-amc:PLAN-ALLOCATION-REFUND-1")
        )
        allocations = list(
            db.scalars(
                select(RepaymentAllocation).where(RepaymentAllocation.recovery_entry_id == payment_entry.entry_id)
            )
        )
        refund_allocations = list(
            db.scalars(
                select(RepaymentAllocation).where(RepaymentAllocation.recovery_entry_id == refund_entry.entry_id)
            )
        )
        assert payment_entry is not None and "PLAN-C008-NEW" in payment_entry.allocation
        assert refund_entry is not None and "冲销" in refund_entry.allocation
        assert sorted(row.amount_cents for row in allocations) == [60_000, 240_000]
        assert sorted(row.amount_cents for row in refund_allocations) == [-60_000, -40_000]


def test_rejected_plan_does_not_change_case_or_accept_payments(client: TestClient) -> None:
    proposed = propose(client).json()
    rejected = decide(client, proposed["id"], 1, decision="reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C008"))
        plan = db.get(RepaymentPlan, proposed["id"])
        installments = list(db.scalars(select(RepaymentInstallment).where(RepaymentInstallment.plan_row_id == plan.id)))
        assert case is not None and case.has_signed_plan is False
        assert plan is not None and sum(row.paid_cents for row in installments) == 0
