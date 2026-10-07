from datetime import timedelta

import pytest
from sqlalchemy import select
from test_loan_collection import BASE, H, enroll, start
from test_loan_collection import client as client

from app.domain import utcnow
from app.loan_models import LoanContactPolicy, LoanProfile, LoanSession
from app.models import CaseRecord, TenantMembership
from app.worker import DatabaseWorker


def queued(client):
    enroll(client)
    row = start(client).json()
    response = client.post(BASE + f"/sessions/{row['id']}/dispatch-check", headers=H,
                           json={"expected_version": row["version"], "acknowledged": True})
    assert response.status_code == 202, response.text
    return row, response.json()["job"]


def run(client, job):
    worker = DatabaseWorker(client.app.state.Session, worker_id="loan-gate-test")
    assert worker.run_once()
    response = client.get(f"/api/v1/jobs/{job['id']}", headers=H).json()
    assert response["status"] == "succeeded", response
    return response["result"]


def test_persistent_worker_check_never_dials_and_duplicate_does_not_enqueue(client):
    row, job = queued(client)
    repeat = client.post(BASE + f"/sessions/{row['id']}/dispatch-check", headers=H,
                         json={"expected_version": row["version"], "acknowledged": True}).json()
    assert not repeat["created"] and repeat["job"]["id"] == job["id"]
    result = run(client, job)
    assert result["gate_passed"] and not result["external_execution"] and not result["provider_ready"]


@pytest.mark.parametrize("change", ["protection", "policy", "profile", "expiry", "role", "dialogue"])
def test_worker_rechecks_changes_after_enqueue(client, change):
    row, job = queued(client)
    with client.app.state.Session() as db:
        session = db.get(LoanSession, row["id"])
        if change == "protection":
            case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C004"))
            case.blocked = True
        elif change == "policy":
            db.get(LoanContactPolicy, "TENANT_A").version += 1
        elif change == "profile":
            db.scalar(select(LoanProfile).where(LoanProfile.tenant_id == "TENANT_A")).version += 1
        elif change == "expiry":
            session.authorization_expires_at = utcnow() - timedelta(seconds=1)
        elif change == "role":
            db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == "TENANT_A",
                                                   TenantMembership.user_id == "Terry")).role = "operator"
        else:
            session.version += 1
            session.state = "ended"
        db.commit()
    result = run(client, job)
    assert not result["gate_passed"] and result["blockers"] and not result["external_execution"]


def test_dispatch_check_is_tenant_scoped_and_requires_admin(client):
    row, _ = queued(client)
    url = BASE + f"/sessions/{row['id']}/dispatch-check"
    body = {"expected_version": row["version"], "acknowledged": True}
    assert client.post(url, headers={"X-Tenant-ID": "TENANT_B", "X-Actor-ID": "Terry"}, json=body).status_code == 404
    with client.app.state.Session() as db:
        db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == "TENANT_A",
                                               TenantMembership.user_id == "Terry")).role = "operator"
        db.commit()
    assert client.post(url, headers=H, json=body).status_code == 403
