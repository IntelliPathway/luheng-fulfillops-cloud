from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class StartupConfigurationError(RuntimeError):
    """Raised before startup when a deployment would weaken production boundaries."""


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise StartupConfigurationError(f"{name} 必须是 true 或 false")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or "change_me" in normalized or "replace-with" in normalized


@dataclass(frozen=True)
class StartupSettings:
    environment: str
    database_url: str
    seed_demo_data: bool
    auto_create_schema: bool
    auth_mode: str
    allow_dev_header_auth: bool
    allow_dev_token: bool

    @classmethod
    def from_environment(
        cls,
        database_url: str | None = None,
        *,
        seed_demo_data: bool | None = None,
    ) -> StartupSettings:
        environment = os.getenv("APP_ENV", "development").strip().lower()
        if environment not in {"development", "test", "staging", "production"}:
            raise StartupConfigurationError("APP_ENV 只能是 development、test、staging 或 production")

        settings = cls(
            environment=environment,
            database_url=database_url or os.getenv("DATABASE_URL", "sqlite:///./luheng-dev.db"),
            seed_demo_data=(
                seed_demo_data
                if seed_demo_data is not None
                else env_bool("SEED_DEMO_DATA", environment == "development")
            ),
            auto_create_schema=env_bool("AUTO_CREATE_SCHEMA", environment != "production"),
            auth_mode=os.getenv("AUTH_MODE", "development").strip().lower(),
            allow_dev_header_auth=env_bool("ALLOW_DEV_HEADER_AUTH", environment == "development"),
            allow_dev_token=env_bool("ALLOW_DEV_TOKEN", environment == "development"),
        )
        settings.validate()
        return settings

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if not self.production:
            return

        errors: list[str] = []
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            errors.append("DATABASE_URL 必须指向 PostgreSQL")
        if self.auth_mode != "oidc":
            errors.append("AUTH_MODE 必须是 oidc")
        if self.allow_dev_header_auth:
            errors.append("ALLOW_DEV_HEADER_AUTH 必须关闭")
        if self.allow_dev_token:
            errors.append("ALLOW_DEV_TOKEN 必须关闭")
        if self.seed_demo_data:
            errors.append("SEED_DEMO_DATA 必须关闭")
        if self.auto_create_schema:
            errors.append("AUTO_CREATE_SCHEMA 必须关闭")

        issuer = os.getenv("OIDC_ISSUER", "").strip()
        audience = os.getenv("OIDC_AUDIENCE", "").strip()
        jwks_url = os.getenv("OIDC_JWKS_URL", "").strip()
        parsed_jwks = urlparse(jwks_url)
        if _is_placeholder(issuer):
            errors.append("OIDC_ISSUER 未配置")
        if _is_placeholder(audience):
            errors.append("OIDC_AUDIENCE 未配置")
        if (
            _is_placeholder(jwks_url)
            or parsed_jwks.scheme != "https"
            or not parsed_jwks.netloc
            or parsed_jwks.username
            or parsed_jwks.password
        ):
            errors.append("OIDC_JWKS_URL 必须是无内嵌凭证的 HTTPS 地址")

        cors_origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()]
        if not cors_origins or any(
            origin == "*" or urlparse(origin).scheme != "https" or not urlparse(origin).netloc
            for origin in cors_origins
        ):
            errors.append("CORS_ORIGINS 必须是一个或多个明确的 HTTPS 来源")

        runtime_secret = os.getenv("RUNTIME_JWT_SECRET", "")
        if _is_placeholder(runtime_secret) or len(runtime_secret) < 32:
            errors.append("RUNTIME_JWT_SECRET 必须是至少 32 字符的独立随机密钥")

        secret_backend = os.getenv("SECRET_STORE_BACKEND", "reference-only").strip().lower()
        if secret_backend == "local-envelope" and _is_placeholder(os.getenv("SECRET_MASTER_KEY", "")):
            errors.append("local-envelope 需要有效的 SECRET_MASTER_KEY")

        if errors:
            raise StartupConfigurationError("生产启动配置不安全：" + "；".join(errors))
