import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.membership_governance import MembershipGovernanceError, decide_proposal
from app.models import AuditEvent, TenantMembership


def headers(actor: str = "Terry", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_membership_change_requires_independent_admin_and_is_audited() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        members = client.get("/api/v1/governance/members", headers=headers()).json()
        assert {member["user_id"] for member in members} >= {"Terry", "test-operator"}

        created = client.post(
            "/api/v1/governance/membership-proposals",
            headers=headers(),
            json={
                "target_user_id": "test-operator",
                "requested_role": "viewer",
                "requested_status": "active",
                "proposal_reason": "试点期间将运营账号调整为只读观察权限",
                "acknowledged": True,
            },
        )
        assert created.status_code == 201, created.text
        proposal = created.json()
        self_review = client.post(
            f"/api/v1/governance/membership-proposals/{proposal['id']}/decision",
            headers=headers(),
            json={
                "decision": "approve",
                "expected_version": 1,
                "review_note": "尝试自行批准本人提交的成员变更",
                "acknowledged": True,
            },
        )
        assert self_review.status_code == 403

        approved = client.post(
            f"/api/v1/governance/membership-proposals/{proposal['id']}/decision",
            headers=headers("test-user"),
            json={
                "decision": "approve",
                "expected_version": 1,
                "review_note": "已独立核对试点职责与最小权限要求",
                "acknowledged": True,
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        with client.app.state.Session() as db:
            membership = db.scalar(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == "TENANT_A",
                    TenantMembership.user_id == "test-operator",
                )
            )
            assert membership and membership.role == "viewer"
            actions = set(
                db.scalars(select(AuditEvent.action).where(AuditEvent.resource_type == "membership_proposal"))
            )
            assert actions == {"membership.proposed", "membership.approved"}


def test_membership_governance_is_tenant_scoped_and_protects_last_admin() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        hidden = client.get("/api/v1/governance/membership-proposals", headers=headers(tenant="TENANT_B"))
        assert hidden.status_code == 200
        assert hidden.json() == []

        with client.app.state.Session() as db:
            rows = list(
                db.scalars(
                    select(TenantMembership).where(
                        TenantMembership.tenant_id == "TENANT_B",
                        TenantMembership.role == "admin",
                        TenantMembership.user_id != "Terry",
                    )
                )
            )
            for row in rows:
                row.status = "inactive"
            db.commit()

        created = client.post(
            "/api/v1/governance/membership-proposals",
            headers=headers(tenant="TENANT_B"),
            json={
                "target_user_id": "Terry",
                "requested_role": "viewer",
                "requested_status": "active",
                "proposal_reason": "验证系统保护最后一名有效管理员",
                "acknowledged": True,
            },
        ).json()
        with client.app.state.Session() as db, pytest.raises(MembershipGovernanceError) as denied:
            decide_proposal(
                db,
                "TENANT_B",
                "independent-reviewer",
                created["id"],
                "approve",
                1,
                "尝试降级最后一名有效管理员账号",
            )
        assert denied.value.code == "last_admin_protected"
