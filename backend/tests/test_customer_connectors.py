import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_customer_materials import headers

from app import customer_connectors as connectors
from app.jobs import execute_job
from app.main import create_app
from app.models import CustomerConnector, CustomerSyncEvent, ManagedSecret, TenantMembership


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    monkeypatch.setenv("CUSTOMER_CONNECTOR_EGRESS_ALLOWLIST", "customer.example")
    monkeypatch.setattr(
        connectors,
        "fetch_page",
        lambda endpoint, token, cursor: page() if not cursor else {"events": [], "next_cursor": cursor},
    )
    with TestClient(create_app("sqlite:///:memory:")) as c:
        yield c


def config():
    return {
        "name": "测试客户",
        "endpoint": "https://customer.example/events",
        "credential": "synthetic-api-token",
        "mapping": {"case_id": "case", "payment_cents": "pay", "refund_cents": "refund"},
        "acknowledged": True,
    }


def page():
    return {
        "events": [
            {
                "event_id": "EVENT-1",
                "source_reference": "BANK/TEST-001",
                "records": [{"case": "C002", "pay": 0, "refund": 0}],
            }
        ],
        "next_cursor": "checkpoint-1",
    }


def enabled(c):
    r = c.post("/api/v1/customer-connectors", headers=headers(), json=config())
    assert r.status_code == 201, r.text
    row = r.json()
    body = {"expected_version": row["version"], "acknowledged": True}
    assert c.post(f"/api/v1/customer-connectors/{row['id']}/enable", headers=headers(), json=body).status_code == 409
    assert c.post(f"/api/v1/customer-connectors/{row['id']}/test", headers=headers(), json=body).status_code == 200
    r = c.post(f"/api/v1/customer-connectors/{row['id']}/enable", headers=headers(), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_connector_encryption_gates_worker_and_mapping(client):
    c = client
    row = enabled(c)
    assert "synthetic-api-token" not in str(row) and "secret_ref" not in row
    with c.app.state.Session() as db:
        secret = db.scalar(select(ManagedSecret))
        assert "synthetic-api-token" not in secret.ciphertext
    r = c.post(f"/api/v1/customer-connectors/{row['id']}/sync", headers=headers()).json()
    assert (
        c.post(f"/api/v1/customer-connectors/{row['id']}/sync", headers=headers()).json()["job"]["id"] == r["job"]["id"]
    )
    execute_job(c.app.state.Session, r["job"]["id"])
    event = c.get("/api/v1/customer-sync/events", headers=headers()).json()[0]
    execute_job(c.app.state.Session, event["job_id"])
    assert c.get("/api/v1/customer-sync/events", headers=headers()).json()[0]["material_id"]
    assert c.get("/api/v1/customer-connectors", headers=headers("test-viewer", "TENANT_B")).json() == []
    assert c.post(f"/api/v1/customer-connectors/{row['id']}/sync", headers=headers("test-viewer")).status_code == 403
    assert (
        c.post(f"/api/v1/customer-connectors/{row['id']}/sync", headers=headers("test-viewer", "TENANT_B")).status_code
        == 403
    )
    body = config() | {"credential": None, "expected_version": row["version"], "name": "修改名称"}
    changed = c.put(f"/api/v1/customer-connectors/{row['id']}", headers=headers(), json=body).json()
    assert not changed["enabled"] and not changed["test_current"] and changed["version"] == 2


def test_partial_page_replay_does_not_skip_or_duplicate(client, monkeypatch):
    c = client
    row = enabled(c)
    p = page()
    p["events"].append(p["events"][0] | {"event_id": "EVENT-2"})
    monkeypatch.setattr(connectors, "fetch_page", lambda *args: p)
    original = connectors.persist_event

    def partial(event, context, db):
        if event.external_event_id == "EVENT-2":
            raise HTTPException(503, "synthetic transient failure")
        return original(event, context, db)

    monkeypatch.setattr(connectors, "persist_event", partial)
    job = c.post(f"/api/v1/customer-connectors/{row['id']}/sync", headers=headers()).json()["job"]
    execute_job(c.app.state.Session, job["id"])
    with c.app.state.Session() as db:
        assert db.get(CustomerConnector, row["id"]).cursor == ""
        assert len(db.scalars(select(CustomerSyncEvent)).all()) == 1
    monkeypatch.setattr(connectors, "persist_event", original)
    assert c.post(f"/api/v1/jobs/{job['id']}/retry", headers=headers()).status_code == 202
    execute_job(c.app.state.Session, job["id"])
    with c.app.state.Session() as db:
        assert db.get(CustomerConnector, row["id"]).cursor == "checkpoint-1"
        assert len(db.scalars(select(CustomerSyncEvent)).all()) == 2


def test_retest_failure_permissions_and_scheduling(client, monkeypatch):
    c = client
    row = enabled(c)
    connectors.schedule_due(c.app.state.Session)
    job = c.get("/api/v1/customer-connectors", headers=headers()).json()[0]["job"]
    assert job["status"] == "queued"
    with c.app.state.Session() as db:
        member = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == "TENANT_A", TenantMembership.user_id == "Terry"
            )
        )
        member.role = "viewer"
        db.commit()
    execute_job(c.app.state.Session, job["id"])
    assert c.get("/api/v1/customer-connectors", headers=headers()).json()[0]["job"]["status"] == "failed"

    def unavailable(*args):
        raise HTTPException(503, "unavailable")

    monkeypatch.setattr(connectors, "fetch_page", unavailable)
    assert (
        c.post(
            f"/api/v1/customer-connectors/{row['id']}/test",
            headers=headers("test-user"),
            json={"expected_version": 1, "acknowledged": True},
        ).status_code
        == 503
    )
    current = c.get("/api/v1/customer-connectors", headers=headers()).json()[0]
    assert not current["test_current"] and not current["enabled"]


def test_ssrf_response_contract_and_amount_validation(client, monkeypatch):
    for endpoint in [
        "http://customer.example/events",
        "https://other.example/events",
        "https://customer.example:abc/events",
        "https://token@customer.example/events",
    ]:
        assert (
            client.post(
                "/api/v1/customer-connectors", headers=headers(), json=config() | {"endpoint": endpoint}
            ).status_code
            == 422
        )
    monkeypatch.setattr(connectors.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(HTTPException):
        connectors.public_addresses("customer.example")
    row = enabled(client)
    for p in [
        {"events": [], "next_cursor": None},
        page() | {"next_cursor": ""},
        page() | {"events": [page()["events"][0] | {"records": [{"case": "C002", "pay": True, "refund": 0}]}]},
    ]:
        monkeypatch.setattr(connectors, "fetch_page", lambda *args, p=p: p)
        assert (
            client.post(
                f"/api/v1/customer-connectors/{row['id']}/test",
                headers=headers(),
                json={"expected_version": 1, "acknowledged": True},
            ).status_code
            == 422
        )


def test_transport_pins_public_address_and_rejects_redirects(monkeypatch):
    import json

    monkeypatch.setenv("CUSTOMER_CONNECTOR_EGRESS_ALLOWLIST", "customer.example")
    monkeypatch.setattr(connectors, "public_addresses", lambda host: ["93.184.216.34"])
    captured = []

    class Connection:
        status = 200

        def __init__(self, host, address):
            captured.append((host, address))

        def request(self, method, path, headers):
            captured.append((method, path, headers))

        def getresponse(self):
            return self

        def read(self, count):
            return json.dumps(page()).encode()

        def close(self):
            pass

    monkeypatch.setattr(connectors, "PinnedHTTPS", Connection)
    assert connectors.fetch_page("https://customer.example/events", "synthetic-token", "a/b") == page()
    assert captured[0] == ("customer.example", "93.184.216.34")
    assert "cursor=a%2Fb" in captured[1][1] and captured[1][2]["Authorization"] == "Bearer synthetic-token"
    Connection.status = 302
    with pytest.raises(HTTPException) as exc:
        connectors.fetch_page("https://customer.example/events", "synthetic-token", "")
    assert "synthetic-token" not in exc.value.detail
