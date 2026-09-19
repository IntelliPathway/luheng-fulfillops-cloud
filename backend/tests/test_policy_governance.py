from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AssetPackage, AuditEvent


@pytest.fixture()
def client() -> TestClient:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def headers(actor: str = "test-operator", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def payload(version: int = 1) -> dict:
    return {
        "expected_policy_version": version,
        "budget_limit_yuan": 42,
        "min_settlement_bps": 7200,
        "max_installments": 8,
        "min_down_payment_bps": 1800,
        "proposal_reason": "根据资产包结构和二十五项确定性场景重新评估授权边界",
        "acknowledged": True,
    }


def test_policy_proposal_requires_independent_admin_and_publishes_atomically(client: TestClient) -> None:
    created = client.post("/api/v1/policy-proposals/packages/PKG_A", headers=headers(), json=payload())
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert proposal["status"] == "pending_review"
    assert proposal["evaluation"]["scenario_passed"] == 25

    self_review = client.post(
        f"/api/v1/policy-proposals/{proposal['id']}/decision",
        headers=headers("test-operator"),
        json={
            "decision": "approve",
            "expected_version": 1,
            "review_note": "本人确认复核并尝试批准该策略提案",
            "acknowledged": True,
        },
    )
    assert self_review.status_code in {403, 422}

    approved = client.post(
        f"/api/v1/policy-proposals/{proposal['id']}/decision",
        headers=headers("Terry"),
        json={
            "decision": "approve",
            "expected_version": 1,
            "review_note": "已独立核验策略参数、样本评估结果与资产包适用范围",
            "acknowledged": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    app = client.app
    with app.state.Session() as db:
        package = db.scalar(select(AssetPackage).where(AssetPackage.package_id == "PKG_A"))
        assert package is not None
        assert package.policy_version == 2
        assert package.policy_status == "published"
        assert package.budget_limit_yuan == 42
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.resource_type == "policy_proposal")))
        assert {event.action for event in events} == {"policy.proposed", "policy.approved"}


def test_policy_governance_is_tenant_scoped_and_rejects_stale_versions(client: TestClient) -> None:
    hidden = client.post(
        "/api/v1/policy-proposals/packages/PKG_A",
        headers=headers(tenant="TENANT_B"),
        json=payload(),
    )
    assert hidden.status_code == 404

    stale = client.post(
        "/api/v1/policy-proposals/packages/PKG_A",
        headers=headers(),
        json=payload(version=99),
    )
    assert stale.status_code == 409


def test_pending_policy_proposal_blocks_duplicate_review_queue_items(client: TestClient) -> None:
    first = client.post("/api/v1/policy-proposals/packages/PKG_B", headers=headers(), json=payload())
    assert first.status_code == 201
    duplicate = client.post("/api/v1/policy-proposals/packages/PKG_B", headers=headers("test-user"), json=payload())
    assert duplicate.status_code == 409
    rows = client.get("/api/v1/policy-proposals?package_id=PKG_B", headers=headers("test-viewer")).json()
    assert len(rows) == 1
    assert rows[0]["evidence_digest"] == first.json()["evidence_digest"]
