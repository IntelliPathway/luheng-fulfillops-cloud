import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_customer_materials import headers, payload

from app.jobs import execute_job
from app.main import create_app
from app.models import AsyncJob, CustomerMaterial, CustomerSyncEvent, TenantMembership


@pytest.fixture()
def synced(monkeypatch):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        yield client


def event():
    return {"source_system": "test-bank", "external_event_id": "EVENT-001", "material": payload()}


def test_sync_encryption_idempotency_and_worker(synced):
    c = synced
    r = c.post("/api/v1/customer-sync/webhook", headers=headers(), json=event())
    assert r.status_code == 202, r.text
    e = r.json()
    assert e["status"] == "queued"
    assert c.post("/api/v1/customer-sync/events", headers=headers(), json=event()).json()["id"] == e["id"]
    changed = event() | {"material": payload(b"case,pay,refund\nC002,0,0\n")}
    assert c.post("/api/v1/customer-sync/events", headers=headers(), json=changed).status_code == 409
    with c.app.state.Session() as db:
        row = db.get(CustomerSyncEvent, e["id"])
        assert "content_base64" not in row.ciphertext
        assert db.get(AsyncJob, e["job_id"]).payload == {"sync_event_id": e["id"]}
    execute_job(c.app.state.Session, e["job_id"])
    rows = c.get("/api/v1/customer-sync/events", headers=headers()).json()
    assert rows[0]["status"] == "succeeded" and rows[0]["material_id"]
    with c.app.state.Session() as db:
        assert len(list(db.scalars(select(CustomerMaterial)))) == 1
    execute_job(c.app.state.Session, e["job_id"])
    assert c.get("/api/v1/customer-sync/events", headers=headers("test-viewer", "TENANT_B")).json() == []
    assert c.post("/api/v1/customer-sync/events", headers=headers("test-viewer"), json=event()).status_code == 403
    assert c.post("/api/v1/customer-sync/webhook", json=event()).status_code == 401


def test_sync_failure_retry_and_revoked_permission(synced, monkeypatch):
    c = synced
    e = c.post("/api/v1/customer-sync/events", headers=headers(), json=event()).json()
    monkeypatch.delenv("SECRET_MASTER_KEY")
    execute_job(c.app.state.Session, e["job_id"])
    assert c.get("/api/v1/customer-sync/events", headers=headers()).json()[0]["status"] == "failed"
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    assert c.post(f"/api/v1/jobs/{e['job_id']}/retry", headers=headers()).status_code == 202
    execute_job(c.app.state.Session, e["job_id"])
    assert c.get("/api/v1/customer-sync/events", headers=headers()).json()[0]["status"] == "succeeded"
    e2 = c.post(
        "/api/v1/customer-sync/events", headers=headers(), json=event() | {"external_event_id": "EVENT-002"}
    ).json()
    with c.app.state.Session() as db:
        m = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == "TENANT_A", TenantMembership.user_id == "Terry"
            )
        )
        m.role = "viewer"
        db.commit()
    execute_job(c.app.state.Session, e2["job_id"])
    assert c.get("/api/v1/customer-sync/events", headers=headers()).json()[0]["status"] == "failed"
