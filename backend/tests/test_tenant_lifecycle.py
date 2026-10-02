from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "Terry", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def propose(client: TestClient, target: str, expected: int) -> dict:
    response = client.post(
        "/api/v1/platform/lifecycle/proposals",
        headers=headers(),
        json={
            "target_stage": target,
            "expected_lifecycle_version": expected,
            "region": "ap-southeast-1",
            "data_retention_days": 730,
            "contract_reference": "contract://pilot/2026-09" if target == "active" else None,
            "customer_success_owner": "Terry",
            "proposal_reason": f"企业租户生命周期转换到 {target}",
            "acknowledged": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def approve(client: TestClient, proposal: dict) -> dict:
    response = client.post(
        f"/api/v1/platform/lifecycle/proposals/{proposal['id']}/decision",
        headers=headers("test-user"),
        json={
            "decision": "approve",
            "expected_version": proposal["version"],
            "review_note": "已独立核验合同、数据保留和访问影响",
            "acknowledged": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_tenant_lifecycle_requires_maker_checker_and_suspends_entitlements() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        default = client.get("/api/v1/platform/lifecycle", headers=headers("test-viewer")).json()
        assert default["stage"] == "trial"
        assert default["version"] == 0
        plan = client.put(
            "/api/v1/platform/subscription",
            headers=headers(),
            json={"plan_code": "enterprise", "expected_version": 0, "acknowledged": True},
        )
        assert plan.status_code == 200

        activation = propose(client, "active", 0)
        self_review = client.post(
            f"/api/v1/platform/lifecycle/proposals/{activation['id']}/decision",
            headers=headers(),
            json={
                "decision": "approve",
                "expected_version": 1,
                "review_note": "不能自行复核",
                "acknowledged": True,
            },
        )
        assert self_review.status_code == 409
        approve(client, activation)
        active = client.get("/api/v1/platform/lifecycle", headers=headers("test-viewer")).json()
        assert active["stage"] == "active"
        assert active["version"] == 1

        suspension = propose(client, "suspended", 1)
        approve(client, suspension)
        denied = client.post(
            "/api/v1/agents/sessions",
            headers=headers("test-viewer"),
            json={"scope_type": "global", "title": "暂停后不应创建"},
        )
        assert denied.status_code == 403
        assert "subscription_inactive" in denied.text


def test_lifecycle_transition_and_tenant_scope_fail_closed() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        missing_contract = client.post(
            "/api/v1/platform/lifecycle/proposals",
            headers=headers(),
            json={
                "target_stage": "active",
                "expected_lifecycle_version": 0,
                "region": "ap-southeast-1",
                "data_retention_days": 365,
                "proposal_reason": "缺少合同引用时必须阻断激活",
                "acknowledged": True,
            },
        )
        assert missing_contract.status_code == 422
        tenant_b = client.get(
            "/api/v1/platform/lifecycle",
            headers=headers("test-viewer", "TENANT_B"),
        ).json()
        assert tenant_b["stage"] == "trial"
        assert tenant_b["version"] == 0
