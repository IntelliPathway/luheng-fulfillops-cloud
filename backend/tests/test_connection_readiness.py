from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.connection_readiness import build_connection_readiness, https_origin
from app.main import create_app

ORIGIN = "https://repayguard.example.com"


def test_connection_diagnostics_auth_tenant_origin_and_readonly_boundary(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    with TestClient(create_app("sqlite:///:memory:")) as client:
        path = "/api/v1/pilot/connection-readiness"
        assert client.get(path).status_code == 401
        reports = []
        for tenant in ["TENANT_A", "TENANT_B"]:
            headers = {"X-Tenant-ID": tenant, "X-Actor-ID": "test-viewer", "Origin": ORIGIN}
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            report = response.json()
            assert report["tenant_id"] == tenant
            assert report["status"] == "blocked"
            assert report["real_business_verified"] is False
            assert report["enables_external_execution"] is False
            checks = {c["id"]: c for c in report["checks"]}
            assert checks["site-origin"]["passed"]
            assert not checks["enterprise-session"]["passed"]
            assert not checks["production-profile"]["passed"]
            assert not checks["postgresql"]["passed"]
            assert len(report["report_digest"]) == 64
            assert client.get(path, headers=headers).json()["report_digest"] == report["report_digest"]
            reports.append(report)
        assert reports[0]["report_digest"] != reports[1]["report_digest"]
        headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
        for origin in [None, "https://other.example.com", "null", "https://user:secret@example.com"]:
            response = client.get(path, headers={**headers, **({"Origin": origin} if origin else {})})
            checks = {c["id"]: c for c in response.json()["checks"]}
            assert not checks["site-origin"]["passed"]
            assert "secret@" not in response.text
        # Use the immutable middleware snapshot, not a later environment change.
        monkeypatch.setenv("CORS_ORIGINS", "https://other.example.com")
        report = client.get(path, headers={**headers, "Origin": "https://other.example.com"}).json()
        assert not next(c for c in report["checks"] if c["id"] == "site-origin")["passed"]
        assert client.post(path, headers=headers).status_code == 405


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://example.com/",
        "https://a:b@example.com",
        "https://example.com/path",
        "https://example.com?secret=abc",
        "https://example.com#token",
        "https://example.com:99999",
        "null",
        None,
    ],
)
def test_invalid_origin_is_not_exported(value):
    assert https_origin(value) is None


def test_ready_infrastructure_does_not_claim_business_acceptance(monkeypatch):
    # Isolated report logic only; this fixture does not connect actual production infrastructure.
    preflight = {
        "generated_at": "2026-10-06T00:00:00Z",
        "configuration_digest": "a" * 64,
        "checks": [
            {"id": key, "passed": True, "detail": {}}
            for key in ["migrations", "worker-heartbeat", "independent-admins", "tenant-active"]
        ]
        + [{"id": "acceptance", "passed": False, "detail": {"status": "blocked"}}],
    }
    monkeypatch.setattr("app.connection_readiness.build_pilot_preflight", lambda *_: preflight)
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    context = SimpleNamespace(tenant_id="ISOLATED_TEST", auth_mode="oidc")
    startup = SimpleNamespace(
        environment="production",
        seed_demo_data=False,
        auto_create_schema=False,
        allow_dev_header_auth=False,
        allow_dev_token=False,
        auth_mode="oidc",
    )
    report = build_connection_readiness(db, context, startup, ORIGIN, [ORIGIN])
    assert report["status"] == "ready_for_pilot"
    assert report["business_acceptance_status"] == "blocked"
    assert report["real_business_verified"] is False
    assert report["enables_external_execution"] is False
    context.auth_mode = "development"
    assert build_connection_readiness(db, context, startup, ORIGIN, [ORIGIN])["status"] == "blocked"


def test_same_origin_browser_claim_is_allowed_only_without_origin_header(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    with TestClient(create_app("sqlite:///:memory:")) as client:
        headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
        path = "/api/v1/pilot/connection-readiness"
        response = client.get(path, params={"browser_origin": ORIGIN}, headers=headers)
        report = response.json()
        assert response.status_code == 200
        assert next(c for c in report["checks"] if c["id"] == "site-origin")["passed"]
        assert report["origin_evidence"] == "browser_claim_and_server_allowlist_only"
        for claim in ["https://foreign.example.com", "https://user:secret@example.com", "null"]:
            report = client.get(path, params={"browser_origin": claim}, headers=headers).json()
            assert not next(c for c in report["checks"] if c["id"] == "site-origin")["passed"]
            assert "secret@" not in str(report)
        report = client.get(path, params={"browser_origin": ORIGIN}, headers={**headers, "Origin": "null"}).json()
        assert not next(c for c in report["checks"] if c["id"] == "site-origin")["passed"]
        assert report["origin_evidence"] == "origin_header_and_server_allowlist_only"


@pytest.mark.parametrize("has_records", [False, True])
def test_incomplete_postgres_migrations_skip_current_schema_queries(monkeypatch, has_records):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.migrations import _migration_files

    engine = create_engine("sqlite:///:memory:")
    # Execute real SQL against an intentionally incomplete schema; emulate only the dialect label.
    monkeypatch.setattr(engine.dialect, "name", "postgresql")
    if has_records:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)"))
            for path in _migration_files():
                if path.name != "027_pilot_evidence.sql":
                    connection.execute(text("INSERT INTO schema_migrations VALUES (:version)"), {"version": path.name})
    with Session(engine) as db:
        report = build_connection_readiness(
            db,
            SimpleNamespace(tenant_id="ISOLATED_TEST", auth_mode="oidc"),
            SimpleNamespace(
                environment="production",
                seed_demo_data=False,
                auto_create_schema=False,
                allow_dev_header_auth=False,
                allow_dev_token=False,
                auth_mode="oidc",
            ),
            None,
            [ORIGIN],
            browser_origin=ORIGIN,
        )
    assert report["status"] == "blocked"
    checks = {c["id"]: c for c in report["checks"]}
    assert "027_pilot_evidence.sql" in checks["migrations"]["detail"]["missing"]
    if has_records:
        assert checks["migrations"]["detail"]["missing"] == ["027_pilot_evidence.sql"]
    assert checks["worker-heartbeat"]["detail"]["reason"] == "migrations_pending"
    assert report["business_acceptance_status"] == "unavailable"
    assert report["configuration_digest"] is None
    engine.dispose()
