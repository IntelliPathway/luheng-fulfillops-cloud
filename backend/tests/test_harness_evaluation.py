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
