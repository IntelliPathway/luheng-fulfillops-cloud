from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import urlparse

import jwt
from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select

from .models import Tenant, TenantMembership, User

ROLES = {"viewer", "operator", "admin"}
ROLE_RANK = {"viewer": 10, "operator": 20, "admin": 30}
OIDC_ASYMMETRIC_ALGORITHMS = {
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
}


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    actor_id: str
    role: str
    display_name: str
    email: str
    auth_mode: str


@dataclass(frozen=True)
class RuntimeGrant:
    tenant_id: str
    session_id: str
    scope_type: str
    scope_id: str | None


@dataclass(frozen=True)
class AuthConfiguration:
    mode: str
    status: str
    issuer_configured: bool
    audience_configured: bool
    jwks_configured: bool
    allowed_algorithms: tuple[str, ...]
    required_claims: tuple[str, ...]
    tenant_claim: str | None
    leeway_seconds: int
    jwks_cache_seconds: int
    dev_header_enabled: bool
    dev_token_enabled: bool
    detail: str


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _development_default() -> bool:
    return os.getenv("APP_ENV", "development").strip().lower() == "development"


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} 必须在 {minimum} 到 {maximum} 之间",
        )
    return value


def _auth_mode() -> str:
    configured = os.getenv("AUTH_MODE", "auto").strip().lower()
    if configured not in {"auto", "oidc", "shared-secret", "development"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AUTH_MODE 配置无效")
    if configured != "auto":
        return configured
    if os.getenv("OIDC_JWKS_URL", "").strip():
        return "oidc"
    if os.getenv("AUTH_JWT_SECRET", "").strip():
        return "shared-secret"
    return "development" if _truthy(os.getenv("ALLOW_DEV_TOKEN"), _development_default()) else "unconfigured"


def _oidc_algorithms() -> tuple[str, ...]:
    algorithms = tuple(
        dict.fromkeys(item.strip() for item in os.getenv("OIDC_ALLOWED_ALGORITHMS", "RS256").split(",") if item.strip())
    )
    if not algorithms or any(item not in OIDC_ASYMMETRIC_ALGORITHMS for item in algorithms):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC_ALLOWED_ALGORITHMS 只允许受支持的非对称签名算法",
        )
    return algorithms


def _required_claims() -> tuple[str, ...]:
    claims = tuple(
        dict.fromkeys(
            item.strip() for item in os.getenv("OIDC_REQUIRED_CLAIMS", "sub,exp,iat").split(",") if item.strip()
        )
    )
    required = {"sub", "exp", "iat"}
    if not required.issubset(claims):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC_REQUIRED_CLAIMS 必须至少包含 sub、exp 和 iat",
        )
    return claims


def _oidc_jwks_url() -> str:
    jwks_url = os.getenv("OIDC_JWKS_URL", "").strip()
    parsed = urlparse(jwks_url)
    insecure_allowed = _truthy(os.getenv("OIDC_ALLOW_INSECURE_JWKS"), False)
    allowed_scheme = parsed.scheme == "https" or (insecure_allowed and parsed.scheme == "http")
    if not jwks_url or not parsed.netloc or not allowed_scheme or parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC_JWKS_URL 必须是无内嵌凭证的 HTTPS 地址",
        )
    return jwks_url


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str, cache_seconds: int, timeout_seconds: int) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        jwks_url,
        cache_keys=True,
        max_cached_keys=32,
        cache_jwk_set=True,
        lifespan=cache_seconds,
        timeout=timeout_seconds,
    )


def auth_configuration() -> AuthConfiguration:
    mode = _auth_mode()
    issuer = bool(os.getenv("OIDC_ISSUER", "").strip())
    audience = bool(os.getenv("OIDC_AUDIENCE", "").strip())
    jwks = bool(os.getenv("OIDC_JWKS_URL", "").strip())
    algorithms: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    leeway = 0
    cache_seconds = 300
    detail = "认证尚未配置"
    config_status = "degraded"
    if mode == "oidc":
        algorithms = _oidc_algorithms()
        required = _required_claims()
        if jwks:
            _oidc_jwks_url()
        leeway = _bounded_int("OIDC_LEEWAY_SECONDS", 30, 0, 300)
        cache_seconds = _bounded_int("OIDC_JWKS_CACHE_SECONDS", 300, 60, 86400)
        complete = issuer and audience and jwks
        config_status = "ready" if complete else "degraded"
        detail = "企业 OIDC/JWKS 强校验已就绪" if complete else "OIDC 需要 issuer、audience 与 JWKS URL"
    elif mode == "shared-secret":
        required = ("sub", "exp", "iat")
        complete = bool(os.getenv("AUTH_JWT_SECRET", "").strip()) and issuer and audience
        config_status = "ready" if complete else "degraded"
        detail = "共享密钥 JWT 已配置；生产环境推荐企业 OIDC" if complete else "共享密钥 JWT 配置不完整"
    elif mode == "development":
        required = ("sub", "exp", "iat")
        config_status = "development"
        detail = "仅供本地开发；生产环境必须关闭开发认证"
    tenant_claim = os.getenv("OIDC_TENANT_CLAIM", "").strip() or None
    return AuthConfiguration(
        mode=mode,
        status=config_status,
        issuer_configured=issuer,
        audience_configured=audience,
        jwks_configured=jwks,
        allowed_algorithms=algorithms,
        required_claims=required,
        tenant_claim=tenant_claim,
        leeway_seconds=leeway,
        jwks_cache_seconds=cache_seconds,
        dev_header_enabled=_truthy(os.getenv("ALLOW_DEV_HEADER_AUTH"), _development_default()),
        dev_token_enabled=_truthy(os.getenv("ALLOW_DEV_TOKEN"), _development_default()) and mode != "oidc",
        detail=detail,
    )


def _decode_bearer(token: str) -> tuple[dict[str, Any], str]:
    mode = _auth_mode()
    issuer = os.getenv("OIDC_ISSUER")
    audience = os.getenv("OIDC_AUDIENCE")
    if mode == "oidc":
        jwks_url = _oidc_jwks_url()
        if not (issuer and audience and jwks_url):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bearer OIDC 认证配置不完整")
        algorithms = _oidc_algorithms()
        required = _required_claims()
        leeway = _bounded_int("OIDC_LEEWAY_SECONDS", 30, 0, 300)
        cache_seconds = _bounded_int("OIDC_JWKS_CACHE_SECONDS", 300, 60, 86400)
        timeout_seconds = _bounded_int("OIDC_JWKS_TIMEOUT_SECONDS", 5, 1, 30)
        try:
            signing_key = _jwks_client(jwks_url, cache_seconds, timeout_seconds).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(algorithms),
                audience=audience,
                issuer=issuer,
                leeway=leeway,
                options={"require": list(required)},
            )
            return claims, "oidc"
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌无效或已过期") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC 签名密钥暂时不可用"
            ) from exc

    if mode not in {"shared-secret", "development"}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bearer 认证尚未配置")
    shared_secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if mode == "development":
        issuer = issuer or "luheng-local"
        audience = audience or "luheng-fulfillops"
        if not shared_secret and _truthy(os.getenv("ALLOW_DEV_TOKEN"), _development_default()):
            shared_secret = "luheng-local-development-secret-change-me"
    if not (shared_secret and issuer and audience):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer 认证尚未配置")
    try:
        claims = jwt.decode(
            token,
            shared_secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["sub", "exp", "iat"]},
        )
        return claims, "development-token" if mode == "development" else "shared-secret"
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌无效或已过期") from exc


def issue_dev_token(actor_id: str, expires_minutes: int = 60) -> str:
    if not _truthy(os.getenv("ALLOW_DEV_TOKEN"), _development_default()) or _auth_mode() == "oidc":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="开发令牌入口未启用")
    secret = os.getenv("AUTH_JWT_SECRET", "").strip() or "luheng-local-development-secret-change-me"
    now = datetime.now(UTC)
    payload = {
        "sub": actor_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "iss": os.getenv("OIDC_ISSUER", "").strip() or "luheng-local",
        "aud": os.getenv("OIDC_AUDIENCE", "").strip() or "luheng-fulfillops",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _runtime_secret() -> str:
    secret = os.getenv("RUNTIME_JWT_SECRET") or os.getenv("AUTH_JWT_SECRET")
    if secret:
        return secret
    if _truthy(os.getenv("ALLOW_DEV_TOKEN"), _development_default()):
        return "luheng-local-runtime-secret-change-me"
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Runtime 工具令牌密钥尚未配置")


def issue_runtime_token(
    tenant_id: str,
    session_id: str,
    scope_type: str,
    scope_id: str | None,
    expires_minutes: int = 20,
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": f"runtime:{session_id}",
        "tenant_id": tenant_id,
        "session_id": session_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "token_use": "agent-tool-runtime",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "luheng-agent-gateway",
        "aud": "luheng-harness-tools",
    }
    return jwt.encode(payload, _runtime_secret(), algorithm="HS256"), expires_at


def decode_runtime_token(token: str) -> RuntimeGrant:
    try:
        claims = jwt.decode(
            token,
            _runtime_secret(),
            algorithms=["HS256"],
            audience="luheng-harness-tools",
            issuer="luheng-agent-gateway",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Runtime 工具令牌无效或已过期") from exc
    if claims.get("token_use") != "agent-tool-runtime":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌用途不允许访问 Agent 工具")
    tenant_id = str(claims.get("tenant_id") or "")
    session_id = str(claims.get("session_id") or "")
    scope_type = str(claims.get("scope_type") or "global")
    scope_id = str(claims["scope_id"]) if claims.get("scope_id") is not None else None
    if not tenant_id or not session_id or scope_type not in {"global", "activity", "case"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Runtime 工具令牌声明不完整")
    return RuntimeGrant(tenant_id=tenant_id, session_id=session_id, scope_type=scope_type, scope_id=scope_id)


def request_context(
    request: Request,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> RequestContext:
    if not x_tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少租户上下文")

    auth_mode = "oidc"
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims, auth_mode = _decode_bearer(token)
        actor_id = str(claims.get("sub") or "")
        if not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌缺少 subject")
        if x_actor_id and x_actor_id != actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请求身份与令牌不一致")
        tenant_claim = os.getenv("OIDC_TENANT_CLAIM", "").strip() if auth_mode == "oidc" else ""
        if tenant_claim:
            claim_value = claims.get(tenant_claim)
            allowed_tenants = claim_value if isinstance(claim_value, list) else [claim_value]
            if x_tenant_id not in {str(item) for item in allowed_tenants if item is not None}:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="身份令牌不允许访问当前工作空间")
    else:
        if not _truthy(os.getenv("ALLOW_DEV_HEADER_AUTH"), _development_default()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 Bearer 身份令牌")
        actor_id = x_actor_id or ""
        auth_mode = "development-header"
        if not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少开发环境操作者身份")

    with request.app.state.Session() as db:
        if not db.get(Tenant, x_tenant_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作空间不存在")
        user = db.get(User, actor_id)
        if not user or user.status != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == x_tenant_id,
                TenantMembership.user_id == actor_id,
                TenantMembership.status == "active",
            )
        )
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户不属于当前工作空间")
        if membership.role not in ROLES:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="租户成员角色无效")
        return RequestContext(
            tenant_id=x_tenant_id,
            actor_id=actor_id,
            role=membership.role,
            display_name=user.display_name,
            email=user.email,
            auth_mode=auth_mode,
        )


def require_role(context: RequestContext, *allowed: str) -> None:
    if context.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色无权执行此操作")


def require_at_least(context: RequestContext, minimum: str) -> None:
    if ROLE_RANK.get(context.role, 0) < ROLE_RANK.get(minimum, 999):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色无权确认该行动")
