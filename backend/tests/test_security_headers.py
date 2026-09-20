from fastapi.testclient import TestClient

from app.main import create_app


def test_api_responses_include_defensive_browser_headers() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        response = client.get("/api/v1/health/live")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["permissions-policy"] == "camera=(), geolocation=(), microphone=()"
        assert response.headers["cache-control"] == "no-store"
