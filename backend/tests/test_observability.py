from fastapi.testclient import TestClient

from app.main import create_app


def test_request_id_and_admin_metrics_are_available() -> None:
    app = create_app("sqlite:///:memory:")
    headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "Terry", "X-Request-ID": "trace-test-0001"}
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live", headers=headers)
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "trace-test-0001"
        metrics = client.get("/api/v1/observability/metrics", headers=headers)
        assert metrics.status_code == 200
        payload = metrics.json()
        assert payload["requests_total"] >= 1
        assert payload["requests_by_route"]["GET /api/v1/health/live"] == 1
        assert payload["latency_ms"]["p95"] >= 0


def test_untrusted_request_id_is_replaced() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad value"})
        assert response.headers["X-Request-ID"].startswith("req-")
