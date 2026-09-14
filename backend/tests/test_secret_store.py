from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AgentSession, ManagedSecret
from app.runtime_adapters import runtime_settings_for
from app.secret_store import (
    SecretStoreError,
    resolve_secret,
    rewrap_active_secrets,
    secret_store_status,
    store_secret,
)


def _key(seed: int) -> str:
    return base64.urlsafe_b64encode(bytes((seed + index) % 256 for index in range(32))).decode()


def _configure_envelope(monkeypatch: pytest.MonkeyPatch, seed: int = 1, version: str = "test-v1") -> str:
    encoded = _key(seed)
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", encoded)
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", version)
    monkeypatch.delenv("SECRET_PREVIOUS_KEYS", raising=False)
    return encoded


def test_envelope_store_encrypts_resolves_and_retires(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_envelope(monkeypatch)
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        first_ref, last4 = store_secret(db, "TENANT_A", "model", "deepseek-secret-1234")
        db.commit()
        stored = db.scalar(select(ManagedSecret).where(ManagedSecret.reference == first_ref))
        assert stored is not None
        assert "deepseek-secret" not in stored.ciphertext
        assert last4 == "1234"
        assert resolve_secret(db, first_ref, "TENANT_A", "model") == "deepseek-secret-1234"

        second_ref, _ = store_secret(
            db,
            "TENANT_A",
            "model",
            "rotated-secret-9876",
            previous_reference=first_ref,
        )
        db.commit()
        db.refresh(stored)
        assert stored.status == "retired"
        assert resolve_secret(db, second_ref, "TENANT_A", "model") == "rotated-secret-9876"
        with pytest.raises(SecretStoreError, match="已退役"):
            resolve_secret(db, first_ref, "TENANT_A", "model")


def test_envelope_reference_is_tenant_and_service_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_envelope(monkeypatch)
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        reference, _ = store_secret(db, "TENANT_A", "model", "tenant-bound-secret")
        db.commit()
        with pytest.raises(SecretStoreError):
            resolve_secret(db, reference, "TENANT_B", "model")
        with pytest.raises(SecretStoreError):
            resolve_secret(db, reference, "TENANT_A", "voice")


def test_previous_key_can_decrypt_after_master_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    old_key = _configure_envelope(monkeypatch, seed=3, version="key-v1")
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        reference, _ = store_secret(db, "TENANT_A", "model", "rotatable-secret")
        db.commit()
        monkeypatch.setenv("SECRET_MASTER_KEY", _key(9))
        monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "key-v2")
        monkeypatch.setenv("SECRET_PREVIOUS_KEYS", json.dumps({"key-v1": old_key}))
        assert resolve_secret(db, reference, "TENANT_A", "model") == "rotatable-secret"
        assert secret_store_status(db, "TENANT_A").rotation_pending == 1
        assert rewrap_active_secrets(db, "TENANT_A") == 1
        db.commit()
        monkeypatch.delenv("SECRET_PREVIOUS_KEYS")
        assert resolve_secret(db, reference, "TENANT_A", "model") == "rotatable-secret"
        assert secret_store_status(db, "TENANT_A").rotation_pending == 0


def test_missing_master_key_is_reported_without_accepting_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.delenv("SECRET_MASTER_KEY", raising=False)
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        with pytest.raises(SecretStoreError, match="SECRET_MASTER_KEY"):
            store_secret(db, "TENANT_A", "model", "must-not-persist")
        status = secret_store_status(db, "TENANT_A")
        assert status.status == "degraded"
        assert status.resolvable is False


def test_secret_store_health_and_api_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_envelope(monkeypatch)
    app = create_app("sqlite:///:memory:")
    headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
    with TestClient(app) as client:
        current = client.get("/api/v1/integrations", headers=headers).json()["services"]["model"]
        response = client.put(
            "/api/v1/integrations/model",
            headers=headers,
            json={
                "provider": current["provider"],
                "settings": current["settings"],
                "credential": "api-envelope-secret-2468",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["services"]["model"]["credential_mask"] == "••••2468"
        assert "api-envelope-secret" not in response.text
        health = client.get("/api/v1/security/secrets/health", headers=headers)
        assert health.status_code == 200
        assert health.json() == {
            "backend": "local-envelope",
            "status": "ready",
            "persistent": True,
            "resolvable": True,
            "key_version": "test-v1",
            "active_secrets": 1,
            "rotation_pending": 0,
            "detail": "AES-256-GCM 信封存储已就绪；主密钥不进入数据库",
        }
        with app.state.Session() as db:
            session = AgentSession(
                tenant_id="TENANT_A",
                created_by="test-user",
                runtime_provider="Hermes Agent",
                runtime_profile="fulfill-agent-v3",
                title="secret injection test",
            )
            settings = runtime_settings_for(db, session)
            assert settings["_modelCredential"] == "api-envelope-secret-2468"
        operator = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-operator"}
        assert client.get("/api/v1/security/secrets/health", headers=operator).status_code == 403
