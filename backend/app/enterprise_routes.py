"""Authenticated workspace discovery and fresh enterprise onboarding evidence."""

import hashlib
import json
import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import func, select

from .dependencies import Context, Database
from .integration_acceptance import build_integration_acceptance
from .models import AssetImportBatch, CustomerMaterial, Tenant, TenantMembership, User
from .security import ROLES, _decode_bearer, _development_default, _truthy

router = APIRouter(prefix="/api/v1", tags=["enterprise-onboarding"])


@router.get("/auth/workspaces")
def workspaces(
    db: Database,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
):
    response.headers["Cache-Control"] = "no-store"
    allowed = None
    if authorization and authorization.lower().startswith("bearer "):
        claims, mode = _decode_bearer(authorization.split(" ", 1)[1].strip())
        actor = str(claims.get("sub") or "")
        if not actor or (x_actor_id and x_actor_id != actor):
            raise HTTPException(401, "请求身份与令牌不一致")
        tenant_claim = os.getenv("OIDC_TENANT_CLAIM", "").strip() if mode == "oidc" else ""
        if tenant_claim:
            value = claims.get(tenant_claim)
            allowed = {str(v) for v in (value if isinstance(value, list) else [value]) if v is not None}
    elif _truthy(os.getenv("ALLOW_DEV_HEADER_AUTH"), _development_default()) and x_actor_id:
        actor = x_actor_id
    else:
        raise HTTPException(401, "需要 Bearer 身份令牌")
    user = db.get(User, actor)
    if not user or user.status != "active":
        raise HTTPException(401, "用户不存在或已停用")
    rows = db.execute(
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(TenantMembership.user_id == actor, TenantMembership.status == "active", TenantMembership.role.in_(ROLES))
        .order_by(Tenant.id)
    )
    return {
        "schema_version": 1,
        "items": [
            {"tenant_id": t.id, "tenant_name": t.name, "role": role}
            for t, role in rows
            if allowed is None or t.id in allowed
        ],
    }


@router.get("/enterprise/onboarding")
def onboarding(context: Context, db: Database, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    report = build_integration_acceptance(
        db,
        context,
        request.app.state.startup,
        request.headers.get("origin"),
        request.app.state.cors_origins,
        request.query_params.get("browser_origin"),
    )
    admins = db.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .join(User, User.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == context.tenant_id,
            TenantMembership.status == "active",
            TenantMembership.role == "admin",
            User.status == "active",
        )
    )
    imports = db.scalar(
        select(func.count())
        .select_from(AssetImportBatch)
        .where(
            AssetImportBatch.tenant_id == context.tenant_id,
            AssetImportBatch.status == "committed",
            AssetImportBatch.committed_by.is_not(None),
            AssetImportBatch.committed_by != AssetImportBatch.created_by,
        )
    )
    materials = db.scalar(
        select(func.count())
        .select_from(CustomerMaterial)
        .where(
            CustomerMaterial.tenant_id == context.tenant_id,
        )
    )
    connection = report["connection"]["status"] == "ready_for_pilot"
    accepted = report["customer_cases"]["current_accepted"]
    steps = [
        {
            "id": "identity",
            "label": "企业成员与独立审核",
            "complete": admins >= 2,
            "action": "settings",
            "detail": f"当前有效管理员 {admins} 位；账号由身份服务认证，业务角色由成员关系裁决。",
        },
        {
            "id": "connection",
            "label": "正式后台与登录连接",
            "complete": connection,
            "action": "connection",
            "detail": "核验生产配置、当前 OIDC 会话、数据库、Worker 与站点来源。",
        },
        {
            "id": "imports",
            "label": "案件与委托导入",
            "complete": imports > 0,
            "action": "assets",
            "detail": f"独立复核并提交的导入批次 {imports} 个；有效委托按案件另行检查。",
        },
        {
            "id": "materials",
            "label": "材料接入",
            "complete": materials > 0,
            "action": "materials",
            "detail": f"已保存材料 {materials} 份；来源、解密完整性与最新版本在案件验收中复核。",
        },
        {
            "id": "acceptance",
            "label": "关联、对账与独立验收",
            "complete": accepted > 0,
            "action": "acceptance",
            "detail": f"当前有效验收案件 {accepted} 个；分别核对付款与退款，退款可以为零。",
        },
    ]
    body = {
        "schema_version": 1,
        "tenant_id": context.tenant_id,
        "generated_at": report["generated_at"],
        "steps": steps,
        "status": report["status"],
        "integration_report_digest": report["report_digest"],
        "real_business_verified": False,
        "enables_external_execution": False,
    }
    return body | {"report_digest": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}
