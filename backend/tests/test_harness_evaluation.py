from fastapi.testclient import TestClient

from app.main import create_app


def headers(tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": "Terry"}


def test_harness_catalog_is_explicit_about_available_and_planned_adapters() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        payload = client.get("/api/v1/harnesses/catalog", headers=headers()).json()
        statuses = {row["id"]: row["status"] for row in payload["adapters"]}
        assert statuses["hermes"] == "available"
        assert statuses["deepseek-harness"] == "available"
        assert statuses["agentscope"] == "planned"
        assert statuses["pi-agent"] == "planned"


def test_harness_leaderboard_is_tenant_scoped_and_exposes_scoring_policy() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        tenant_a = client.get("/api/v1/harnesses/leaderboard", headers=headers()).json()
        tenant_b = client.get("/api/v1/harnesses/leaderboard", headers=headers("TENANT_B")).json()
        assert tenant_a["weights"] == {"quality": 0.7, "reliability": 0.2, "cost_efficiency": 0.1}
        assert tenant_a["tenant_id"] == "TENANT_A"
        assert tenant_b["tenant_id"] == "TENANT_B"
        assert tenant_a["results"] != tenant_b["results"] or not tenant_a["results"]


def test_harness_plugin_requires_verification_and_independent_approval() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        registered = client.post(
            "/api/v1/harnesses/plugins",
            headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"},
            json={
                "plugin_key": "agentscope-safe",
                "display_name": "AgentScope Safe",
                "protocol": "contract-adapter",
                "entrypoint_reference": "plugin://agentscope/fulfillops-safe",
                "capabilities": ["multi-agent", "evaluation", "tool-use"],
                "acknowledged": True,
            },
        )
        assert registered.status_code == 201, registered.text
        plugin = registered.json()
        verified = client.post(
            f"/api/v1/harnesses/plugins/{plugin['id']}/verify",
            headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"},
            json={"expected_version": 1, "acknowledged": True},
        )
        assert verified.status_code == 200, verified.text
        self_approval = client.post(
            f"/api/v1/harnesses/plugins/{plugin['id']}/approve",
            headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"},
            json={"expected_version": 2, "acknowledged": True},
        )
        assert self_approval.status_code == 409
        approved = client.post(
            f"/api/v1/harnesses/plugins/{plugin['id']}/approve",
            headers=headers(),
            json={"expected_version": 2, "acknowledged": True},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "enabled"
        selection = client.post(
            "/api/v1/harnesses/select",
            headers=headers(),
            json={"required_capabilities": ["multi-agent"], "objective": "balanced"},
        ).json()
        assert selection["selection"]["key"] == "agentscope-safe"
        assert selection["evidence_level"] == "catalog-only"
        assert selection["production_eligible"] is False


def test_harness_plugin_rejects_unapproved_capabilities() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        denied = client.post(
            "/api/v1/harnesses/plugins",
            headers=headers(),
            json={
                "plugin_key": "unsafe-shell",
                "display_name": "Unsafe Shell",
                "protocol": "json-rpc",
                "entrypoint_reference": "plugin://unsafe/shell",
                "capabilities": ["shell"],
                "acknowledged": True,
            },
        )
        assert denied.status_code == 422
        assert client.get("/api/v1/harnesses/catalog", headers=headers("TENANT_B")).json()["plugins"] == []
