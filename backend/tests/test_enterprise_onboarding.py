import pytest
from fastapi.testclient import TestClient

from app.enterprise_provision import EnterpriseManifest, provision_enterprise
from app.main import create_app
from app.models import TenantMembership, User


def manifest(tenant):
    return EnterpriseManifest(
        tenant_id=tenant,
        tenant_name=tenant,
        members=[
            {"subject": tenant + suffix, "email": tenant + suffix + "@example.com", "display_name": suffix}
            for suffix in ("-admin", "-reviewer")
        ],
    )


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("ALLOW_DEV_HEADER_AUTH", "true")
    app = create_app("sqlite:///:memory:", seed_demo_data=False)
    with TestClient(app) as c:
        with app.state.Session() as db:
            provision_enterprise(db, manifest("FIRST"))
            provision_enterprise(db, manifest("SECOND"))
        yield c


def test_first_and_second_enterprise_workspace_discovery_and_isolation(client):
    path = "/api/v1/auth/workspaces"
    assert client.get(path).status_code == 401
    result = client.get(path, headers={"X-Actor-ID": "FIRST-admin"})
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["items"] == [{"tenant_id": "FIRST", "tenant_name": "FIRST", "role": "admin"}]
    for tenant in ["FIRST", "SECOND"]:
        h = {"X-Tenant-ID": tenant, "X-Actor-ID": tenant + "-admin"}
        r = client.get("/api/v1/enterprise/onboarding", headers=h)
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["tenant_id"] == tenant
        assert report["status"] == "blocked"
        assert not report["real_business_verified"] and not report["enables_external_execution"]
        assert next(s["complete"] for s in report["steps"] if s["id"] == "identity")
        assert not next(s["complete"] for s in report["steps"] if s["id"] == "imports")
        assert (
            client.get(
                "/api/v1/enterprise/onboarding",
                headers=h | {"X-Tenant-ID": ("SECOND" if tenant == "FIRST" else "FIRST")},
            ).status_code
            == 403
        )


def test_removed_members_and_disabled_users_cannot_discover_workspaces(client):
    with client.app.state.Session() as db:
        member = db.query(TenantMembership).filter_by(user_id="FIRST-admin").one()
        member.status = "revoked"
        db.commit()
    assert client.get("/api/v1/auth/workspaces", headers={"X-Actor-ID": "FIRST-admin"}).json()["items"] == []
    with client.app.state.Session() as db:
        db.get(User, "FIRST-admin").status = "disabled"
        db.commit()
    assert client.get("/api/v1/auth/workspaces", headers={"X-Actor-ID": "FIRST-admin"}).status_code == 401


def test_oidc_claim_is_intersected_with_memberships(client, monkeypatch):
    import app.enterprise_routes as routes

    monkeypatch.setenv("OIDC_TENANT_CLAIM", "organizations")
    monkeypatch.setattr(
        routes, "_decode_bearer", lambda token: ({"sub": "FIRST-admin", "organizations": ["SECOND"]}, "oidc")
    )
    assert (
        client.get("/api/v1/auth/workspaces", headers={"Authorization": "Bearer synthetic-test"}).json()["items"] == []
    )
    monkeypatch.setattr(
        routes, "_decode_bearer", lambda token: ({"sub": "FIRST-admin", "organizations": ["FIRST"]}, "oidc")
    )
    assert (
        len(client.get("/api/v1/auth/workspaces", headers={"Authorization": "Bearer synthetic-test"}).json()["items"])
        == 1
    )
    assert (
        client.get(
            "/api/v1/auth/workspaces", headers={"Authorization": "Bearer synthetic-test", "X-Actor-ID": "SECOND-admin"}
        ).status_code
        == 401
    )


def test_two_enterprises_import_and_materials_follow_independent_review(client, monkeypatch):
    import base64

    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    for tenant in ["FIRST", "SECOND"]:
        h = {"X-Tenant-ID": tenant, "X-Actor-ID": tenant + "-admin"}
        csv = (
            "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,"
            "commission_rule_id,commission_rate_bps,contact_basis_ref\n"
            f"{tenant}_PKG,合成验证资产,{tenant}_CASE,10000,2026-01-01,2027-12-31,{tenant}_RULE,1000,SYNTHETIC-AUTHORITY"
        )
        preview = client.post(
            "/api/v1/asset-imports/previews",
            headers=h,
            json={"filename": "synthetic.csv", "csv_text": csv, "idempotency_key": "same-key-is-tenant-scoped"},
        )
        assert preview.status_code == 201, preview.text
        batch = preview.json()
        body = {"expected_version": batch["version"], "review_note": "独立核验合成测试字段与金额", "acknowledged": True}
        path = f"/api/v1/asset-imports/{batch['id']}/commit"
        assert client.post(path, headers=h, json=body).status_code == 403
        assert client.post(path, headers=h | {"X-Actor-ID": tenant + "-reviewer"}, json=body).status_code == 200
        raw = f"case_id,payment_cents,refund_cents\n{tenant}_CASE,0,0\n".encode()
        material = client.post(
            "/api/v1/customer-materials",
            headers=h,
            json={
                "filename": "synthetic-amounts.csv",
                "source_reference": "SYNTHETIC/INTERNAL",
                "file_kind": "csv",
                "content_base64": base64.b64encode(raw).decode(),
                "mapping": {"case_id": "case_id", "payment_cents": "payment_cents", "refund_cents": "refund_cents"},
                "acknowledged": True,
            },
        )
        assert material.status_code == 201, material.text
        report = client.get("/api/v1/enterprise/onboarding", headers=h).json()
        assert next(s["complete"] for s in report["steps"] if s["id"] == "imports")
        assert next(s["complete"] for s in report["steps"] if s["id"] == "materials")
        assert not next(s["complete"] for s in report["steps"] if s["id"] == "acceptance")
        other = "SECOND" if tenant == "FIRST" else "FIRST"
        assert (
            client.get(
                f"/api/v1/customer-materials/{material.json()['id']}/content",
                headers={"X-Tenant-ID": other, "X-Actor-ID": other + "-admin"},
            ).status_code
            == 404
        )
