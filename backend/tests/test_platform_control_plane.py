from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "test-user", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_platform_defaults_are_authoritative_and_usage_is_tenant_scoped() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        subscription = client.get("/api/v1/platform/subscription", headers=headers("test-viewer")).json()
        assert subscription["plan_code"] == "team"
        assert subscription["version"] == 0
        usage = client.get("/api/v1/platform/usage", headers=headers("test-viewer")).json()
        assert usage["meters"]["seats"]["used"] == 4
        assert usage["meters"]["managed_cases"]["used"] > 0
        other = client.get("/api/v1/platform/usage", headers=headers("test-viewer", "TENANT_B")).json()
        assert other["meters"]["managed_cases"]["used"] != usage["meters"]["managed_cases"]["used"]


def test_admin_can_version_plan_and_capabilities_follow_entitlements() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        updated = client.put(
            "/api/v1/platform/subscription",
            headers=headers(),
            json={"plan_code": "enterprise", "expected_version": 0, "acknowledged": True},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 1
        capabilities = client.get("/api/v1/platform/capabilities", headers=headers("test-viewer")).json()
        assert capabilities["capabilities"]["enterprise-oidc"] is True
        stale = client.put(
            "/api/v1/platform/subscription",
            headers=headers(),
            json={"plan_code": "pilot", "expected_version": 0, "acknowledged": True},
        )
        assert stale.status_code == 409
