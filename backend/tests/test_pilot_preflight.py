from fastapi.testclient import TestClient

from app.main import create_app


def test_preflight_is_readonly_tenant_scoped_and_blocks_demo():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        for tenant in ["TENANT_A", "TENANT_B"]:
            r = client.get("/api/v1/pilot/preflight", headers={"X-Tenant-ID": tenant, "X-Actor-ID": "test-viewer"})
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["tenant_id"] == tenant
            assert data["status"] == "blocked"
            assert data["enables_external_execution"] is False
            checks = {c["id"]: c for c in data["checks"]}
            assert not checks["migrations"]["passed"]
            assert "027_pilot_evidence.sql" in checks["migrations"]["detail"]["missing"]
            assert not checks["worker-heartbeat"]["passed"]
            assert not checks["tenant-active"]["passed"]
        assert client.get("/api/v1/pilot/preflight").status_code == 401
