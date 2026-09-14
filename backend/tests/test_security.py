from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import app.security as security_module
from app.main import create_app


class FakeSigningKey:
    def __init__(self, key) -> None:
        self.key = key


class FakeJwksClient:
    def __init__(self, public_key) -> None:
        self.public_key = public_key
        self.calls = 0

    def get_signing_key_from_jwt(self, _: str) -> FakeSigningKey:
        self.calls += 1
        return FakeSigningKey(self.public_key)


def _configure_oidc(monkeypatch: pytest.MonkeyPatch) -> tuple[object, FakeJwksClient]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake = FakeJwksClient(private_key.public_key())
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("ALLOW_DEV_HEADER_AUTH", "false")
    monkeypatch.setenv("ALLOW_DEV_TOKEN", "false")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("OIDC_AUDIENCE", "luheng-fulfillops")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://idp.example.test/.well-known/jwks.json")
    monkeypatch.setenv("OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("OIDC_REQUIRED_CLAIMS", "sub,exp,iat,jti")
    monkeypatch.setenv("OIDC_TENANT_CLAIM", "tenant_ids")
    monkeypatch.setenv("OIDC_LEEWAY_SECONDS", "15")
    monkeypatch.setenv("OIDC_JWKS_CACHE_SECONDS", "600")
    monkeypatch.setattr(security_module, "_jwks_client", lambda *_: fake)
    return private_key, fake


def _token(private_key, **overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "test-user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "iss": "https://idp.example.test",
        "aud": "luheng-fulfillops",
        "jti": "oidc-test-token-1",
        "tenant_ids": ["TENANT_A"],
        **overrides,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-rsa-1"})


def _headers(token: str, tenant_id: str = "TENANT_A") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_id}


def test_development_token_uses_local_claim_defaults_with_custom_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("ALLOW_DEV_TOKEN", "true")
    monkeypatch.setenv("AUTH_JWT_SECRET", "custom-local-secret")
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    monkeypatch.delenv("OIDC_AUDIENCE", raising=False)

    token = security_module.issue_dev_token("test-user")
    claims, auth_mode = security_module._decode_bearer(token)

    assert claims["sub"] == "test-user"
    assert claims["iss"] == "luheng-local"
    assert claims["aud"] == "luheng-fulfillops"
    assert auth_mode == "development-token"


def test_enterprise_oidc_validates_claims_and_reports_safe_health(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key, fake = _configure_oidc(monkeypatch)
    app = create_app("sqlite:///:memory:")
    token = _token(private_key)
    with TestClient(app) as client:
        session = client.get("/api/v1/auth/session", headers=_headers(token))
        assert session.status_code == 200, session.text
        assert session.json()["auth_mode"] == "oidc"
        assert session.json()["role"] == "admin"
        health = client.get("/api/v1/security/auth/health", headers=_headers(token))
        assert health.status_code == 200, health.text
        assert health.json() == {
            "mode": "oidc",
            "status": "ready",
            "issuer_configured": True,
            "audience_configured": True,
            "jwks_configured": True,
            "allowed_algorithms": ["RS256"],
            "required_claims": ["sub", "exp", "iat", "jti"],
            "tenant_claim": "tenant_ids",
            "leeway_seconds": 15,
            "jwks_cache_seconds": 600,
            "dev_header_enabled": False,
            "dev_token_enabled": False,
            "detail": "企业 OIDC/JWKS 强校验已就绪",
        }
        assert fake.calls == 2
        assert client.post("/api/v1/auth/dev-token", json={"actor_id": "test-user"}).status_code == 404


def test_enterprise_oidc_fails_closed_for_audience_claim_algorithm_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, _ = _configure_oidc(monkeypatch)
    app = create_app("sqlite:///:memory:")
    now = datetime.now(UTC)
    with TestClient(app) as client:
        wrong_audience = _token(private_key, aud="another-service")
        assert client.get("/api/v1/auth/session", headers=_headers(wrong_audience)).status_code == 401

        missing_iat = _token(private_key)
        decoded = jwt.decode(missing_iat, options={"verify_signature": False})
        decoded.pop("iat")
        missing_iat = jwt.encode(decoded, private_key, algorithm="RS256", headers={"kid": "test-rsa-1"})
        assert client.get("/api/v1/auth/session", headers=_headers(missing_iat)).status_code == 401

        hs_payload = {
            "sub": "test-user",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "iss": "https://idp.example.test",
            "aud": "luheng-fulfillops",
            "jti": "hs-confusion",
            "tenant_ids": ["TENANT_A"],
        }
        hs_token = jwt.encode(hs_payload, "not-an-oidc-key", algorithm="HS256", headers={"kid": "test-rsa-1"})
        assert client.get("/api/v1/auth/session", headers=_headers(hs_token)).status_code == 401

        tenant_token = _token(private_key, tenant_ids=["TENANT_A"])
        denied = client.get("/api/v1/auth/session", headers=_headers(tenant_token, "TENANT_B"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "身份令牌不允许访问当前工作空间"


def test_oidc_configuration_rejects_symmetric_algorithm(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_oidc(monkeypatch)
    monkeypatch.setenv("OIDC_ALLOWED_ALGORITHMS", "RS256,HS256")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/session", headers=_headers("not-a-jwt"))
        assert response.status_code == 503
        assert "非对称" in response.json()["detail"]

    monkeypatch.setenv("OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("OIDC_JWKS_URL", "http://idp.example.test/jwks.json")
    insecure_app = create_app("sqlite:///:memory:")
    with TestClient(insecure_app) as client:
        response = client.get("/api/v1/auth/session", headers=_headers("not-a-jwt"))
        assert response.status_code == 503
        assert "HTTPS" in response.json()["detail"]
