from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import PilotEvidence, ServiceConfig, utcnow


def headers(actor="Terry", tenant="TENANT_A"):
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def propose(client, gate="identity"):
    response = client.post(
        "/api/v1/pilot/evidence",
        headers=headers(),
        json={
            "gate_id": gate,
            "evidence_reference": "ACCEPTANCE/2026-001",
            "evidence_digest": "a" * 64,
            "validity_days": 30,
            "acknowledged": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def decide(client, row, actor="test-user", tenant="TENANT_A", decision="approve"):
    return client.post(
        f"/api/v1/pilot/evidence/{row['id']}/decision",
        headers=headers(actor, tenant),
        json={
            "decision": decision,
            "expected_version": row["version"],
            "acknowledged": True,
        },
    )


def test_evidence_requires_independent_admin_and_tenant_scope():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        row = propose(client)
        assert decide(client, row, "Terry").status_code == 409
        assert decide(client, row, "test-viewer").status_code == 403
        assert decide(client, row, "test-user", "TENANT_B").status_code in {403, 404}
        assert client.get("/api/v1/pilot/evidence", headers=headers("test-viewer", "TENANT_B")).json() == []
        approved = decide(client, row)
        assert approved.status_code == 200, approved.text
        assert approved.json()["effective_status"] == "approved"
        assert approved.json()["reviewed_by"] == "test-user"
        assert decide(client, row).status_code == 409
        gate = client.get("/api/v1/pilot/release-gate", headers=headers()).json()
        assert gate["external"][0]["status"] == "approved"
        assert gate["status"] == "blocked"
        assert gate["enables_external_execution"] is False


def test_configuration_change_expiry_and_new_evidence_invalidate_acceptance(monkeypatch):
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        row = propose(client)
        assert decide(client, row).status_code == 200
        monkeypatch.setenv("PILOT_DEPLOYMENT_REVISION", "new-deployment")
        rows = client.get("/api/v1/pilot/evidence", headers=headers()).json()
        assert rows[0]["effective_status"] == "stale"
        pending = propose(client)
        with app.state.Session() as db:
            evidence = db.get(PilotEvidence, pending["id"])
            evidence.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert decide(client, pending).status_code == 409
        assert decide(client, pending, decision="reject").status_code == 200
        latest = propose(client)
        assert decide(client, latest).status_code == 200
        with app.state.Session() as db:
            from sqlalchemy import select

            config = db.scalar(select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A"))
            config.version += 1
            db.commit()
        gate = client.get("/api/v1/pilot/release-gate", headers=headers()).json()
        assert gate["external"][0]["status"] == "stale"
        newest = propose(client)
        gate = client.get("/api/v1/pilot/release-gate", headers=headers()).json()
        assert gate["external"][0]["evidence"]["id"] == newest["id"]
        assert gate["external"][0]["status"] == "pending_review"


def test_evidence_validation_and_expired_approval():
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/pilot/evidence",
                headers=headers("test-viewer"),
                json={
                    "gate_id": "identity",
                    "evidence_reference": "SAFE-001",
                    "evidence_digest": "a" * 64,
                    "acknowledged": True,
                },
            ).status_code
            == 403
        )
        for reference, digest in [("https://secret.example/?token=abc", "a" * 64), ("SAFE-001", "bad")]:
            assert (
                client.post(
                    "/api/v1/pilot/evidence",
                    headers=headers(),
                    json={
                        "gate_id": "identity",
                        "evidence_reference": reference,
                        "evidence_digest": digest,
                        "acknowledged": True,
                    },
                ).status_code
                == 422
            )
        row = propose(client)
        assert decide(client, row).status_code == 200
        with app.state.Session() as db:
            db.get(PilotEvidence, row["id"]).expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert client.get("/api/v1/pilot/release-gate", headers=headers()).json()["external"][0]["status"] == "expired"
