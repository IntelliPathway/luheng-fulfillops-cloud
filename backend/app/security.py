from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select

from .models import Tenant, TenantMembership, User


ROLES = {"viewer", "operator", "admin"}
ROLE_RANK = {"viewer": 10, "operator": 20, "admin": 30}


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    actor_id: str
    role: str
    display_name: str
    email: str
    auth_mode: str


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _decode_bearer(token: str) -> dict[str, Any]:
    shared_secret = os.getenv("AUTH_JWT_SECRET")
    issuer = os.getenv("OIDC_ISSUER")
    audience = os.getenv("OIDC_AUDIENCE")
    if not shared_secret and _truthy(os.getenv("ALLOW_DEV_TOKEN"), True):
        shared_secret = "luheng-local-development-secret-change-me"
        issuer = issuer or "luheng-local"
        audience = audience or "luheng-fulfillops"
    if shared_secret:
        return jwt.decode(token, shared_secret, algorithms=["HS256"], audience=audience, issuer=issuer)
    jwks_url = os.getenv("OIDC_JWKS_URL")
    if not (issuer and audience and jwks_url):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer 认证尚未配置")
    try:
        signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, signing_key, algorithms=["RS256", "ES256"], audience=audience, issuer=issuer)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌无效或已过期") from exc


def issue_dev_token(actor_id: str, expires_minutes: int = 60) -> str:
    if not _truthy(os.getenv("ALLOW_DEV_TOKEN"), True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="开发令牌入口未启用")
    secret = os.getenv("AUTH_JWT_SECRET", "luheng-local-development-secret-change-me")
    now = datetime.now(UTC)
    payload = {
        "sub": actor_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "iss": os.getenv("OIDC_ISSUER", "luheng-local"),
        "aud": os.getenv("OIDC_AUDIENCE", "luheng-fulfillops"),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


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
        try:
            claims = _decode_bearer(token)
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌无效或已过期") from exc
        actor_id = str(claims.get("sub") or "")
        if not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="身份令牌缺少 subject")
        if x_actor_id and x_actor_id != actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请求身份与令牌不一致")
    else:
        if not _truthy(os.getenv("ALLOW_DEV_HEADER_AUTH"), True):
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
