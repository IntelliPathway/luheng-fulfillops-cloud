from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.bootstrap import bootstrap_database
from app.config import StartupConfigurationError, StartupSettings
from app.db import Base, build_session_factory
from app.models import AuditEvent, CaseRecord, Tenant, TenantMembership
from app.provision import IdentityProvisioningRequest, ProvisioningError, provision_identity
from app.seed import seed_demo_data


def _production_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://luheng:secret@postgres:5432/luheng",
        "AUTO_CREATE_SCHEMA": "false",
        "SEED_DEMO_DATA": "false",
        "AUTH_MODE": "oidc",
        "ALLOW_DEV_HEADER_AUTH": "false",
        "ALLOW_DEV_TOKEN": "false",
        "OIDC_ISSUER": "https://idp.example.test/",
        "OIDC_AUDIENCE": "luheng-fulfillops",
        "OIDC_JWKS_URL": "https://idp.example.test/.well-known/jwks.json",
        "CORS_ORIGINS": "https://fulfillops.example.test",
        "RUNTIME_JWT_SECRET": "runtime-secret-at-least-thirty-two-characters",
        "SECRET_STORE_BACKEND": "reference-only",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_settings_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_environment(monkeypatch)
    settings = StartupSettings.from_environment()
    assert settings.production is True
    assert settings.seed_demo_data is False
    assert settings.auto_create_schema is False
    assert settings.allow_dev_header_auth is False
    assert settings.allow_dev_token is False


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("AUTH_MODE", "development", "AUTH_MODE"),
        ("ALLOW_DEV_HEADER_AUTH", "true", "ALLOW_DEV_HEADER_AUTH"),
        ("SEED_DEMO_DATA", "true", "SEED_DEMO_DATA"),
        ("AUTO_CREATE_SCHEMA", "true", "AUTO_CREATE_SCHEMA"),
        ("DATABASE_URL", "sqlite:///production.db", "PostgreSQL"),
        ("OIDC_JWKS_URL", "http://idp.example.test/jwks.json", "HTTPS"),
        ("CORS_ORIGINS", "*", "CORS_ORIGINS"),
    ],
)
def test_production_rejects_unsafe_overrides(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    _production_environment(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(StartupConfigurationError, match=message):
        StartupSettings.from_environment()


def test_database_bootstrap_seeds_only_when_explicitly_enabled() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    session_factory = build_session_factory(engine)
    settings = StartupSettings(
        environment="test",
        database_url="sqlite://",
        seed_demo_data=False,
        auto_create_schema=True,
        auth_mode="development",
        allow_dev_header_auth=True,
        allow_dev_token=True,
    )
    result = bootstrap_database(engine, session_factory, settings)
    assert result.demo_data_seeded is False
    with session_factory() as db:
        assert db.scalar(select(func.count(Tenant.id))) == 0


def test_demo_seed_refuses_to_mix_with_a_real_tenant() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="REAL_TENANT", name="正式租户"))
        db.commit()
        with pytest.raises(RuntimeError, match="拒绝隐式混入"):
            seed_demo_data(db)


def test_identity_provisioning_is_idempotent_and_has_no_demo_business_data() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    request = IdentityProvisioningRequest(
        tenant_id="CUSTOMER_001",
        tenant_name="客户一",
        user_id="oidc-subject-1",
        email="owner@example.test",
        display_name="首位管理员",
        role="admin",
    )
    with Session(engine) as db:
        first = provision_identity(db, request)
        second = provision_identity(db, request)
        assert first.id == second.id
        assert db.scalar(select(func.count(TenantMembership.id))) == 1
        assert db.scalar(select(func.count(AuditEvent.id))) == 1
        assert db.scalar(select(func.count(CaseRecord.id))) == 0


def test_identity_provisioning_rejects_conflicting_role() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    request = IdentityProvisioningRequest(
        tenant_id="CUSTOMER_001",
        tenant_name="客户一",
        user_id="oidc-subject-1",
        email="owner@example.test",
        display_name="首位管理员",
        role="admin",
    )
    with Session(engine) as db:
        provision_identity(db, request)
        with pytest.raises(ProvisioningError, match="角色"):
            provision_identity(db, IdentityProvisioningRequest(**{**request.__dict__, "role": "viewer"}))
