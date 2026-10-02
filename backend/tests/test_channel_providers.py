from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "test-user", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def config(mode: str = "sandbox") -> dict:
    return {
        "provider": "Twilio Sandbox",
        "endpoint_origin": "https://api.twilio.example.test",
        "credential_reference": "secret://TENANT_A/twilio",
        "callback_reference": "https://callback.example.test/communications/twilio",
        "mode": mode,
        "acknowledged": True,
    }


def test_channel_provider_requires_test_and_independent_approval() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        created = client.put("/api/v1/channel-providers/sms", headers=headers(), json=config())
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "configured"
        tested = client.post(
            "/api/v1/channel-providers/sms/test",
            headers=headers(),
            json={"expected_version": 1, "acknowledged": True},
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "tested"
        self_approval = client.post(
            "/api/v1/channel-providers/sms/approve",
            headers=headers(),
            json={"expected_version": 2, "acknowledged": True},
        )
        assert self_approval.status_code == 409
        approved = client.post(
            "/api/v1/channel-providers/sms/approve",
            headers=headers("Terry"),
            json={"expected_version": 2, "acknowledged": True},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "enabled"
        assert len(approved.json()["evidence_digest"]) == 64


def test_live_provider_test_is_fail_closed_and_tenant_scoped() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        created = client.put("/api/v1/channel-providers/email", headers=headers(), json=config("live")).json()
        denied = client.post(
            "/api/v1/channel-providers/email/test",
            headers=headers(),
            json={"expected_version": created["version"], "acknowledged": True},
        )
        assert denied.status_code == 409
        assert "尚未启用真实 Provider" in denied.text
        assert client.get("/api/v1/channel-providers", headers=headers("test-viewer", "TENANT_B")).json() == []


def test_plaintext_or_insecure_live_configuration_is_rejected() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        payload = config("live")
        payload["endpoint_origin"] = "http://provider.example.test"
        payload["credential_reference"] = "plaintext-secret"
        denied = client.put("/api/v1/channel-providers/phone", headers=headers(), json=payload)
        assert denied.status_code == 422
