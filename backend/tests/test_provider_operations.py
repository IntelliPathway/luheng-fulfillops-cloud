from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "test-operator", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_provider_scorecard_is_tenant_scoped_and_exposes_no_payloads(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_DAILY_COST_USD_PER_TENANT", "2.5")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        tenant_a = client.get("/api/v1/provider-operations/scorecard", headers=headers())
        tenant_b = client.get("/api/v1/provider-operations/scorecard", headers=headers(tenant="TENANT_B"))
        assert tenant_a.status_code == 200
        assert tenant_b.status_code == 200
        payload = tenant_a.json()
        assert payload["tenant_id"] == "TENANT_A"
        assert payload["model_budget"]["daily_limit_usd"] == 2.5
        assert payload["model_budget"]["remaining_usd"] <= 2.5
        assert payload["payment_exceptions"]["count"] >= 0
        serialized = tenant_a.text.lower()
        assert "payload_digest" not in serialized
        assert "signature_digest" not in serialized
        assert tenant_b.json()["tenant_id"] == "TENANT_B"


def test_provider_scorecard_requires_operator_role() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        denied = client.get("/api/v1/provider-operations/scorecard", headers=headers("test-viewer"))
        assert denied.status_code == 403


def test_daily_close_exposes_reconcilable_totals_without_payloads() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        response = client.get("/api/v1/provider-operations/daily-close", headers=headers())
        assert response.status_code == 200
        payload = response.json()
        assert payload["tenant_id"] == "TENANT_A"
        assert payload["unsettled_commission_cents"] >= 0
        assert payload["uncollected_commission_cents"] >= 0
        assert isinstance(payload["balanced"], bool)
        assert "digest" not in response.text.lower()
