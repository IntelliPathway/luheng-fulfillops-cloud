from __future__ import annotations

import hashlib
import json
import os
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .entitlements import EntitlementError, require_capability
from .models import ChannelProviderPilot, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/channel-providers", tags=["channel-providers"])


class ProviderConfig(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    endpoint_origin: str = Field(min_length=8, max_length=255)
    credential_reference: str = Field(min_length=8, max_length=255)
    callback_reference: str = Field(min_length=8, max_length=255)
    mode: Literal["sandbox", "live"]
    acknowledged: bool


class ProviderAction(BaseModel):
    expected_version: int = Field(ge=1)
    acknowledged: bool


def _view(row: ChannelProviderPilot) -> dict:
    return {
        "id": row.id,
        "channel": row.channel,
        "provider": row.provider,
        "endpoint_origin": row.endpoint_origin,
        "credential_reference": row.credential_reference,
        "callback_reference": row.callback_reference,
        "mode": row.mode,
        "status": "blocked" if row.mode == "live" and row.status in {"tested", "enabled"} else row.status,
        "stored_status": row.status,
        "external_delivery_verified": False,
        "test_kind": "sandbox_contract" if row.mode == "sandbox" else "unavailable",
        "version": row.version,
        "evidence_digest": row.evidence_digest,
        "configured_by": row.configured_by,
        "tested_by": row.tested_by,
        "approved_by": row.approved_by,
        "configured_at": row.configured_at,
        "tested_at": row.tested_at,
        "approved_at": row.approved_at,
    }


def _row(db: Database, tenant_id: str, channel: str) -> ChannelProviderPilot:
    result = db.scalar(
        select(ChannelProviderPilot).where(
            ChannelProviderPilot.tenant_id == tenant_id,
            ChannelProviderPilot.channel == channel,
        )
    )
    if not result:
        raise HTTPException(404, "渠道 Provider 配置不存在")
    return result


def _require_multichannel(db: Database, tenant_id: str) -> None:
    try:
        require_capability(db, tenant_id, "multichannel")
    except EntitlementError as exc:
        raise HTTPException(exc.http_status, f"{exc}（{exc.code}）") from exc


@router.get("")
def list_providers(context: Context, db: Database) -> list[dict]:
    rows = db.scalars(
        select(ChannelProviderPilot)
        .where(ChannelProviderPilot.tenant_id == context.tenant_id)
        .order_by(ChannelProviderPilot.channel)
    ).all()
    return [_view(row) for row in rows]


@router.put("/{channel}")
def configure_provider(
    channel: Literal["phone", "sms", "email"],
    payload: ProviderConfig,
    context: Context,
    db: Database,
) -> dict:
    require_role(context, "admin")
    _require_multichannel(db, context.tenant_id)
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认保存配置不会自动发送消息或发起呼叫")
    parsed = urlparse(payload.endpoint_origin)
    if payload.mode == "live" and parsed.scheme != "https":
        raise HTTPException(422, "真实 Provider 端点必须使用 HTTPS")
    if not payload.credential_reference.startswith(("secret://", "aws-secrets-manager://", "kms://")):
        raise HTTPException(422, "只能保存外部密钥引用，不能提交明文凭据")
    row = db.scalar(
        select(ChannelProviderPilot).where(
            ChannelProviderPilot.tenant_id == context.tenant_id,
            ChannelProviderPilot.channel == channel,
        )
    )
    values = payload.model_dump(exclude={"acknowledged"})
    if row is None:
        row = ChannelProviderPilot(
            tenant_id=context.tenant_id, channel=channel, configured_by=context.actor_id, **values
        )
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.status = "configured"
        row.version += 1
        row.evidence_digest = None
        row.configured_by = context.actor_id
        row.tested_by = None
        row.approved_by = None
        row.configured_at = utcnow()
        row.tested_at = None
        row.approved_at = None
    db.flush()
    audit(
        db,
        context,
        "channel_provider.configured",
        "channel_provider",
        row.id,
        {"channel": channel, "mode": row.mode, "version": row.version},
    )
    db.commit()
    return _view(row)


@router.post("/{channel}/test")
def test_provider(
    channel: Literal["phone", "sms", "email"], payload: ProviderAction, context: Context, db: Database
) -> dict:
    require_role(context, "admin")
    _require_multichannel(db, context.tenant_id)
    row = _row(db, context.tenant_id, channel)
    if row.version != payload.expected_version:
        raise HTTPException(409, "渠道配置版本已变化")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认测试仅使用脱敏白名单目标")
    if row.mode == "live" and os.getenv("COMMUNICATION_LIVE_PROVIDER_TESTS_ENABLED", "false").lower() != "true":
        raise HTTPException(409, "部署环境尚未启用真实 Provider 白名单测试")
    if row.mode == "live":
        raise HTTPException(503, "尚未实现真实渠道投递验收适配器；配置摘要或 SIP 回声联调不能代替真实投递证据")
    evidence = {
        "channel": row.channel,
        "provider": row.provider,
        "mode": row.mode,
        "version": row.version,
        "contract": "repayguard-channel-v1",
        "external_delivery": False,
    }
    row.evidence_digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    row.status = "tested"
    row.version += 1
    row.tested_by = context.actor_id
    row.tested_at = utcnow()
    row.approved_by = None
    row.approved_at = None
    audit(
        db,
        context,
        "channel_provider.tested",
        "channel_provider",
        row.id,
        {**evidence, "evidence_digest": row.evidence_digest},
    )
    db.commit()
    return _view(row)


@router.post("/{channel}/approve")
def approve_provider(
    channel: Literal["phone", "sms", "email"], payload: ProviderAction, context: Context, db: Database
) -> dict:
    require_role(context, "admin")
    _require_multichannel(db, context.tenant_id)
    row = _row(db, context.tenant_id, channel)
    if row.version != payload.expected_version or row.status != "tested":
        raise HTTPException(409, "渠道测试证据已失效或状态不允许批准")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认已核验 Provider、白名单、回调与合规规则")
    if row.mode == "live":
        raise HTTPException(503, "历史配置摘要不是当前真实投递证据，不允许批准真实渠道")
    if row.tested_by == context.actor_id or row.configured_by == context.actor_id:
        raise HTTPException(409, "配置或测试执行人不能批准同一渠道")
    row.status = "enabled"
    row.version += 1
    row.approved_by = context.actor_id
    row.approved_at = utcnow()
    audit(
        db,
        context,
        "channel_provider.enabled",
        "channel_provider",
        row.id,
        {"channel": channel, "mode": row.mode, "evidence_digest": row.evidence_digest},
    )
    db.commit()
    return _view(row)
