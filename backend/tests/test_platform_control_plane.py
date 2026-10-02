from fastapi.testclient import TestClient

from app.main import create_app
from app.models import User


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


def test_pilot_plan_enforces_capability_and_seat_limits() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        changed = client.put(
            "/api/v1/platform/subscription",
            headers=headers(),
            json={"plan_code": "pilot", "expected_version": 0, "acknowledged": True},
        )
        assert changed.status_code == 200
        denied = client.post(
            "/api/v1/strategy-experiments",
            headers=headers("test-operator"),
            json={
                "package_id": "PKG_A",
                "name": "不应创建的实验",
                "hypothesis": "Pilot 套餐不包含策略实验能力",
                "candidate_policy_version": 2,
                "allocation_bps": 1000,
                "acknowledged": True,
            },
        )
        assert denied.status_code == 403
        assert "capability_not_entitled" in denied.text

        with client.app.state.Session() as db:
            db.add_all(
                [
                    User(id="pilot-seat-5", email="seat5@example.test", display_name="席位五"),
                    User(id="pilot-seat-6", email="seat6@example.test", display_name="席位六"),
                ]
            )
            db.commit()
        for index, user_id in enumerate(("pilot-seat-5", "pilot-seat-6"), start=5):
            proposal = client.post(
                "/api/v1/governance/membership-proposals",
                headers=headers("Terry"),
                json={
                    "target_user_id": user_id,
                    "requested_role": "viewer",
                    "requested_status": "active",
                    "proposal_reason": f"验证 Pilot 第 {index} 个席位门禁",
                    "acknowledged": True,
                },
            ).json()
            reviewed = client.post(
                f"/api/v1/governance/membership-proposals/{proposal['id']}/decision",
                headers=headers("test-user"),
                json={
                    "decision": "approve",
                    "expected_version": 1,
                    "review_note": "独立核验套餐席位限制",
                    "acknowledged": True,
                },
            )
            assert reviewed.status_code == (200 if index == 5 else 429), reviewed.text
