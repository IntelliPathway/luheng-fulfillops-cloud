"""Tenant sandbox policy: explicit configuration, no implicit execution approval."""
from datetime import UTC
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update

from .loan_models import LoanContactPolicy
from .models import Tenant


def lock_policy_scope(db, tenant):
    # Consistent tenant-before-case lock ordering, also effective on SQLite.
    result = db.execute(update(Tenant).where(Tenant.id == tenant).values(name=Tenant.name))
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="租户不存在")


def policy_for(db, tenant):
    return db.scalar(select(LoanContactPolicy).where(LoanContactPolicy.tenant_id == tenant)
                     .execution_options(populate_existing=True))


def policy_view(row):
    if not row:
        return None
    return {name: getattr(row, name) for name in (
        "tenant_id", "timezone", "window_start_minute", "window_end_minute", "daily_session_limit",
        "snapshot_max_hours", "promise_max_days", "authorization_minutes", "paused",
        "authority_reference", "valid_until", "version", "updated_by", "updated_at",
    )} | {"mode": "sandbox", "source_status": "administrator_assertion", "production_ready": False}


def policy_snapshot(row):
    return {**policy_view(row), "valid_until": row.valid_until.isoformat(),
            "updated_at": row.updated_at.isoformat()}


def policy_blockers(row, now):
    if not row:
        return ["尚未配置租户机催政策"]
    reasons = []
    if row.paused:
        reasons.append("租户机催政策已暂停")
    if now >= row.valid_until:
        reasons.append("租户机催政策已到期")
    if row.timezone != "Asia/Shanghai":
        reasons.append("机催政策时区不受支持")
    else:
        local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(row.timezone))
        minute = local.hour * 60 + local.minute
        if not row.window_start_minute <= minute < row.window_end_minute:
            reasons.append("当前不在租户机催政策时段内")
    return reasons
