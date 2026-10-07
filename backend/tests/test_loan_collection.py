from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_tools import execute_controlled_tool
from app.main import create_app
from app.models import AssetPackage, CaseFinancialProfile, CaseRecord, RecoveryLedgerEntry

H = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "Terry"}
BASE = "/api/v1/loan-collection"


@pytest.fixture
def client():
    with TestClient(create_app("sqlite:///:memory:")) as c:
        with c.app.state.Session() as db:
            case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
            case.blocked = False
            case.contact_basis_ref = "CONTACT-BASIS-C004"
            package = db.scalar(select(AssetPackage).where(AssetPackage.tenant_id == "TENANT_A", AssetPackage.package_id == case.package_id))
            package.policy_status = "published"
            financial = db.scalar(select(CaseFinancialProfile).where(CaseFinancialProfile.tenant_id == "TENANT_A", CaseFinancialProfile.case_id == "C004"))
            financial.mandate_start = datetime.now(UTC).date() - timedelta(days=30)
            financial.mandate_end = datetime.now(UTC).date() + timedelta(days=30)
            financial.claim_balance_cents = 100_000
            db.commit()
        yield c


def enroll(c, **changes):
    data = {"product": "标准无抵押贷款", "due_date": (datetime.now(UTC).date() - timedelta(days=3)).isoformat(),
            "amount_cents": 10_000, "contact_reference": "CONTACT-REF-004", "source_reference": "BANK-SNAPSHOT-004",
            "snapshot_at": datetime.now(UTC).isoformat(), "expected_version": 0, "acknowledged": True}
    return c.put(BASE + "/cases/C004/profile", headers=H, json=data | changes)


def start(c, **changes):
    return c.post(BASE + "/sessions", headers=H, json={"case_id": "C004", "request_key": "request-1",
                    "mode": "sandbox", "acknowledged": True} | changes)


def event(c, row, intent, **changes):
    return c.post(BASE + f"/sessions/{row['id']}/events", headers=H, json={"event_key": f"event-{row['version']}",
            "expected_version": row["version"], "intent": intent, "acknowledged": True} | changes)


def test_no_debt_disclosure_before_verification_and_idempotent_events(client):
    assert enroll(client).status_code == 200
    row = start(client).json()
    assert "amount_cents" not in row
    assert event(client, row, "promise", confirmed=True, amount_cents=5000,
                 due_date=datetime.now(UTC).date().isoformat()).status_code == 422
    verified = event(client, row, "identity_verified")
    assert verified.status_code == 200, verified.text
    assert verified.json()["events"][0]["result"]["amount_cents"] == 10_000
    duplicate = event(client, row, "identity_verified")
    assert duplicate.status_code == 200 and len(duplicate.json()["events"]) == 1
    assert event(client, row, "wrong_person").status_code == 409
    assert event(client, row, "end", event_key="another").status_code == 409


def test_protection_and_changed_profile_pause_active_session(client):
    enroll(client)
    row = start(client).json()
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
        case.blocked = True
        db.commit()
    result = event(client, row, "identity_verified")
    assert result.status_code == 200 and result.json()["state"] == "paused"
    assert "amount_cents" not in result.json()["events"][0]["result"]


def test_mandate_snapshot_scope_and_real_channel_fail_closed(client):
    assert start(client).status_code == 409
    assert enroll(client, snapshot_at=(datetime.now(UTC) - timedelta(days=2)).isoformat()).status_code == 200
    assert start(client).status_code == 409
    assert enroll(client, expected_version=1).status_code == 200
    assert start(client, mode="provider").status_code == 503
    assert start(client).status_code == 201
    assert start(client).status_code == 201
    assert start(client, request_key="second").status_code == 409
    other = client.get(BASE + "/overview", headers=H | {"X-Tenant-ID": "TENANT_B"})
    assert other.json()["sessions"] == []
    row = start(client).json()
    assert client.post(BASE + f"/sessions/{row['id']}/events", headers=H | {"X-Tenant-ID": "TENANT_B"},
                       json={"intent": "end", "event_key": "one", "expected_version": 1, "acknowledged": True}).status_code == 404
    assert client.put(BASE + "/cases/C004/profile", headers=H | {"X-Actor-ID": "test-viewer"}, json={}).status_code in {403,422}


def test_promise_is_separate_from_contract_and_money_and_reconciles_net(client):
    enroll(client)
    row = event(client, start(client).json(), "identity_verified").json()
    assert event(client, row, "promise", amount_cents=1000, due_date=datetime.now(UTC).date().isoformat()).status_code == 422
    row = event(client, row, "promise", confirmed=True, amount_cents=1000,
                due_date=datetime.now(UTC).date().isoformat()).json()
    assert row["promise"]["status"] == "pending"
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
        old_status = case.status
        assert not case.has_signed_plan
        # Synthetic immutable ledger fixture. Production records come only from the existing verified receipt flow.
        db.add(RecoveryLedgerEntry(tenant_id="TENANT_A", entry_id="loan-test-payment", case_id="C004",
               package_id=case.package_id, event_type="payment", amount_cents=1000, eligible_amount_cents=0,
               commission_rule_id="test-rule", commission_rule_version=1, rate_bps=0, commission_cents=0,
               reason="fixture", source="synthetic-test", booked_at=datetime.now(UTC).replace(tzinfo=None)))
        db.commit()
    result = client.post(BASE + f"/sessions/{row['id']}/reconcile", headers=H, json={"acknowledged": True})
    assert result.status_code == 200, result.text
    assert result.json()["promise"]["status"] == "fulfilled"
    assert result.json()["state"] == "ptp_recorded"
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
        assert case.status == old_status and not case.has_signed_plan


@pytest.mark.parametrize("intent", ["wrong_person", "dispute", "complaint", "hardship", "human_requested"])
def test_exception_pauses_sandbox_without_mutating_real_protection(client, intent):
    enroll(client)
    row = start(client).json()
    result = event(client, row, intent)
    assert result.status_code == 200 and result.json()["state"] == "paused"
    with client.app.state.Session() as db:
        assert not db.scalar(select(CaseRecord.blocked).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))


def test_exact_enterprise_case_identifier_and_no_cross_tenant_lookup(client):
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
        db.add(CaseRecord(tenant_id="TENANT_A", case_id="bank-2026_001", package_id=case.package_id,
                          status="待联系", contact_basis_ref="CONTACT-BASIS"))
        db.commit()
        result = execute_controlled_tool(db, "TENANT_A", "case.read", {"case_id": "bank-2026_001"},
                                         scope_type="case", scope_id="bank-2026_001")
        assert "待联系" in result["answer"]["body"]
        other = execute_controlled_tool(db, "TENANT_B", "case.read", {"case_id": "bank-2026_001"})
        assert "未找到" in other["answer"]["body"]


def test_due_promise_job_is_durable_and_rechecks_actor(client):
    from app.jobs import execute_job
    from app.loan_models import LoanSession
    from app.models import AsyncJob, User
    enroll(client)
    row = event(client, start(client).json(), "identity_verified").json()
    row = event(client, row, "promise", confirmed=True, amount_cents=1000,
                due_date=datetime.now(UTC).date().isoformat()).json()
    with client.app.state.Session() as db:
        db.get(LoanSession, row["id"]).authorization_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        job = db.scalar(select(AsyncJob).where(AsyncJob.kind == "loan.promise_check"))
        assert job and job.available_at > datetime.now(UTC).replace(tzinfo=None)
        job.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        job_id = job.id
        db.commit()
    assert start(client, request_key="cannot-bypass-promise").status_code == 409
    execute_job(client.app.state.Session, job_id)
    with client.app.state.Session() as db:
        job = db.get(AsyncJob, job_id)
        assert job.status == "succeeded", job.error
        assert job.result["promise_status"] == "pending"
        job.status = "queued"
        job.available_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.get(User, "Terry").status = "disabled"
        db.commit()
    execute_job(client.app.state.Session, job_id)
    with client.app.state.Session() as db:
        job = db.get(AsyncJob, job_id)
        assert job.status != "succeeded"
        assert "权限" in job.error


@pytest.mark.parametrize("revocation", ["expired", "missing", "disabled", "demoted", "membership_disabled", "tenant_suspended"])
def test_authorization_rechecked_before_new_disclosure(client, revocation):
    from app.loan_models import LoanSession
    from app.models import TenantLifecycle, TenantMembership, User
    enroll(client)
    row = start(client).json()
    assert row["authorization_expires_at"]
    with client.app.state.Session() as db:
        session = db.get(LoanSession, row["id"])
        if revocation == "expired":
            session.authorization_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        elif revocation == "missing":
            session.authorization_expires_at = None
        elif revocation == "disabled":
            db.get(User, "Terry").status = "disabled"
        elif revocation in {"demoted", "membership_disabled"}:
            member = db.scalar(select(TenantMembership).where(
                TenantMembership.tenant_id == "TENANT_A", TenantMembership.user_id == "Terry"))
            if revocation == "demoted":
                member.role = "operator"
            else:
                member.status = "disabled"
        else:
            lifecycle = db.get(TenantLifecycle, "TENANT_A")
            if lifecycle:
                lifecycle.stage = "suspended"
            else:
                db.add(TenantLifecycle(tenant_id="TENANT_A", stage="suspended", updated_by="Terry"))
        db.commit()
    response = client.post(BASE + f"/sessions/{row['id']}/events",
        headers=H | {"X-Actor-ID": "test-operator"}, json={"intent": "identity_verified",
            "event_key": "new", "expected_version": row["version"], "acknowledged": True})
    if revocation == "tenant_suspended" and response.status_code == 403:
        return  # Tenant request gate may reject before the state machine.
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "paused"
    assert "amount_cents" not in response.json()["events"][-1]["result"]


def test_expired_session_replaced_without_renewing_old_idempotent_grant(client):
    from app.loan_models import LoanSession
    enroll(client)
    row = start(client).json()
    with client.app.state.Session() as db:
        expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.get(LoanSession, row["id"]).authorization_expires_at = expired_at
        db.commit()
    duplicate = start(client).json()
    assert duplicate["id"] == row["id"]
    assert datetime.fromisoformat(duplicate["authorization_expires_at"]) == expired_at
    replacement = start(client, request_key="replacement")
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["id"] != row["id"]
    with client.app.state.Session() as db:
        assert db.get(LoanSession, row["id"]).state == "paused"
