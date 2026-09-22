from fastapi.testclient import TestClient

from app.main import create_app


def headers(tenant: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": "test-viewer"}


def test_pilot_scorecard_uses_tenant_scoped_authoritative_sources() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        tenant_a = client.get("/api/v1/pilot/scorecard", headers=headers("TENANT_A"))
        tenant_b = client.get("/api/v1/pilot/scorecard", headers=headers("TENANT_B"))
        assert tenant_a.status_code == 200
        assert tenant_a.json()["tenant_id"] == "TENANT_A"
        assert tenant_a.json()["portfolio"]["case_count"] > tenant_b.json()["portfolio"]["case_count"]
        assert tenant_a.json()["money"]["confirmed_net_recovery_cents"] > 0
        assert "sources" in tenant_a.json()
        assert tenant_b.json()["money"]["confirmed_net_recovery_cents"] == 80_000
        assert tenant_b.json()["money"] != tenant_a.json()["money"]


def test_release_gate_fails_closed_in_development() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        response = client.get("/api/v1/pilot/release-gate", headers=headers("TENANT_A"))
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["automated"]["passed"] < payload["automated"]["total"]
        assert {item["id"] for item in payload["external"]} == {
            "identity",
            "compliance",
            "providers",
            "recovery",
        }
