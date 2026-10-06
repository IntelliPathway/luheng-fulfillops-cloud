"""Read-only deployment diagnostics; no network probes, grants or secret export."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

from .pilot_preflight import build_pilot_preflight
from .version import APP_VERSION


def https_origin(value: str | None) -> str | None:
    try:
        url = urlsplit(value or "")
        if (
            url.scheme != "https"
            or not (value or "").startswith("https://")
            or not url.hostname
            or url.username
            or url.password
            or url.path != ""
            or url.query
            or url.fragment
        ):
            return None
        _ = url.port  # Reject malformed/out-of-range ports without normalizing the middleware allowlist.
        return value
    except ValueError:
        return None


def build_connection_readiness(db, context, startup, origin, allowed_origins, browser_origin=None) -> dict:
    preflight = build_pilot_preflight(db, context.tenant_id, startup)
    request_origin = https_origin(origin if origin is not None else browser_origin)
    production = (
        startup.environment == "production"
        and not startup.seed_demo_data
        and not startup.auto_create_schema
        and not startup.allow_dev_header_auth
        and not startup.allow_dev_token
        and startup.auth_mode == "oidc"
    )
    checks = [
        {
            "id": "production-profile",
            "label": "生产启动配置",
            "passed": production,
            "action": "启用生产配置，关闭开发认证、演示播种与自动建表。",
        },
        {
            "id": "enterprise-session",
            "label": "当前企业身份",
            "passed": context.auth_mode == "oidc",
            "action": "通过企业登录建立当前会话，成员权限由服务端租户关系裁决。",
        },
        {
            "id": "site-origin",
            "label": "请求来源允许配置",
            "passed": bool(request_origin and request_origin in allowed_origins),
            "action": "从正式站点发起检查，后端 CORS 必须明确允许该 HTTPS 来源；仍需浏览器跨域实测。",
        },
        {
            "id": "postgresql",
            "label": "生产数据库",
            "passed": db.get_bind().dialect.name == "postgresql",
            "action": "连接私有 PostgreSQL，先备份再执行版本迁移。",
        },
    ]
    labels = {
        "migrations": ("数据库迁移", "执行部署迁移并核对全部版本记录。"),
        "worker-heartbeat": ("独立 Worker", "启动独立 Worker，检查心跳、租约及过期作业。"),
        "independent-admins": ("独立管理员", "配置两个不同企业账号，由第二位管理员完成复核。"),
        "tenant-active": ("租户激活", "完成租户开通与独立激活复核。"),
    }
    for check in preflight["checks"]:
        if check["id"] in labels:
            label, action = labels[check["id"]]
            checks.append({**check, "label": label, "action": action})
    evidence = {
        "schema_version": 1,
        "app_version": APP_VERSION,
        "tenant_id": context.tenant_id,
        "request_origin": request_origin,
        "checks": checks,
        "configuration_digest": preflight["configuration_digest"],
        "business_acceptance_status": next(
            c["detail"]["status"] for c in preflight["checks"] if c["id"] == "acceptance"
        ),
    }
    return {
        **evidence,
        "status": "ready_for_pilot" if all(c["passed"] for c in checks) else "blocked",
        "generated_at": preflight["generated_at"],
        "report_digest": hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "origin_evidence": "origin_header_and_server_allowlist_only"
        if origin is not None
        else "browser_claim_and_server_allowlist_only",
        "real_business_verified": False,
        "enables_external_execution": False,
    }
