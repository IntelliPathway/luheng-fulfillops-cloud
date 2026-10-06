from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from .audit import audit
from .models import (
    ChannelProviderPilot,
    HarnessPlugin,
    PaymentWebhookConfig,
    PilotEvidence,
    ServiceConfig,
    TenantLifecycle,
    TenantMembership,
    utcnow,
)
from .security import require_role

GateId = Literal["identity", "compliance", "providers", "recovery"]


class EvidenceCreate(BaseModel):
    gate_id: GateId
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,159}$")
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validity_days: int = Field(default=30, ge=1, le=90)
    acknowledged: bool


class EvidenceDecision(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    acknowledged: bool


def configuration_digest(db, tenant_id: str, startup=None) -> str:
    """Bind evidence to non-secret deployment and tenant configuration, never credentials."""
    names = (
        "APP_ENV",
        "AUTH_MODE",
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URL",
        "OIDC_TENANT_CLAIM",
        "OIDC_ALLOWED_ALGORITHMS",
        "OIDC_REQUIRED_CLAIMS",
        "CORS_ORIGINS",
        "JOB_EXECUTION_MODE",
        "SECRET_STORE_BACKEND",
        "ALLOW_DEV_HEADER_AUTH",
        "ALLOW_DEV_TOKEN",
        "AUTO_CREATE_SCHEMA",
        "SEED_DEMO_DATA",
        "ENABLE_LIVE_MODEL_CALLS",
        "MODEL_EGRESS_ALLOWLIST",
        "MODEL_ALLOWED_MODELS",
        "FULFILLOPS_ENABLE_DSH_RUNTIME",
        "ENABLE_PAYMENT_SANDBOX",
        "PILOT_DEPLOYMENT_REVISION",
    )
    lifecycle = db.get(TenantLifecycle, tenant_id)
    snapshot = {
        "environment": {name: os.getenv(name, "") for name in names},
        "database": db.get_bind().dialect.name,
        "startup": None
        if startup is None
        else {
            "environment": startup.environment,
            "auth_mode": startup.auth_mode,
            "allow_dev_header_auth": startup.allow_dev_header_auth,
            "allow_dev_token": startup.allow_dev_token,
            "seed_demo_data": startup.seed_demo_data,
            "auto_create_schema": startup.auto_create_schema,
        },
        "lifecycle_version": lifecycle.version if lifecycle else 0,
        "members": sorted(
            (row.user_id, row.role, row.status)
            for row in db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id))
        ),
        "services": sorted(
            (row.service_type, row.version, row.connected)
            for row in db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id))
        ),
        "channels": sorted(
            (r.channel, r.version, r.status, r.mode)
            for r in db.scalars(select(ChannelProviderPilot).where(ChannelProviderPilot.tenant_id == tenant_id))
        ),
        "payments": sorted(
            (r.provider, r.version, r.active)
            for r in db.scalars(select(PaymentWebhookConfig).where(PaymentWebhookConfig.tenant_id == tenant_id))
        ),
        "plugins": sorted(
            (r.plugin_key, r.version, r.status, r.manifest_digest)
            for r in db.scalars(select(HarnessPlugin).where(HarnessPlugin.tenant_id == tenant_id))
        ),
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def evidence_view(row: PilotEvidence, digest: str) -> dict:
    effective = row.status
    if row.status == "approved":
        if row.expires_at <= utcnow():
            effective = "expired"
        elif row.configuration_digest != digest:
            effective = "stale"
    return {
        name: getattr(row, name)
        for name in (
            "id",
            "gate_id",
            "evidence_reference",
            "evidence_digest",
            "configuration_digest",
            "version",
            "proposed_by",
            "reviewed_by",
            "created_at",
            "reviewed_at",
            "expires_at",
        )
    } | {"status": row.status, "effective_status": effective}


def list_evidence(db, tenant_id: str, startup=None) -> list[dict]:
    digest = configuration_digest(db, tenant_id, startup)
    rows = db.scalars(
        select(PilotEvidence)
        .where(PilotEvidence.tenant_id == tenant_id)
        .order_by(PilotEvidence.created_at.desc(), PilotEvidence.id.desc())
    ).all()
    return [evidence_view(row, digest) for row in rows]


def propose_evidence(db, context, payload: EvidenceCreate, startup=None) -> dict:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认引用的是脱敏验收记录，不含凭据或原始个人信息")
    row = PilotEvidence(
        tenant_id=context.tenant_id,
        gate_id=payload.gate_id,
        evidence_reference=payload.evidence_reference,
        evidence_digest=payload.evidence_digest,
        configuration_digest=configuration_digest(db, context.tenant_id, startup),
        proposed_by=context.actor_id,
        expires_at=utcnow() + timedelta(days=payload.validity_days),
    )
    db.add(row)
    db.flush()
    audit(db, context, "pilot_evidence.proposed", "pilot_evidence", row.id, {"gate_id": row.gate_id})
    db.commit()
    return evidence_view(row, row.configuration_digest)


def decide_evidence(db, context, evidence_id: str, payload: EvidenceDecision, startup=None) -> dict:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认已独立核验外部验收记录与摘要")
    row = db.scalar(
        select(PilotEvidence)
        .where(PilotEvidence.id == evidence_id, PilotEvidence.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "验收证据不存在")
    if row.proposed_by == context.actor_id:
        raise HTTPException(409, "提交人不能复核自己的验收证据")
    if row.status != "pending_review" or row.version != payload.expected_version:
        raise HTTPException(409, "证据已完成或版本已变化")
    digest = configuration_digest(db, context.tenant_id, startup)
    if payload.decision == "approve" and (row.configuration_digest != digest or row.expires_at <= utcnow()):
        raise HTTPException(409, "配置已变化或证据过期，请重新提交")
    # Conditional update also protects the SQLite path against competing decisions.
    result = db.execute(
        update(PilotEvidence)
        .where(
            PilotEvidence.id == row.id,
            PilotEvidence.tenant_id == context.tenant_id,
            PilotEvidence.version == payload.expected_version,
            PilotEvidence.status == "pending_review",
        )
        .values(
            status="approved" if payload.decision == "approve" else "rejected",
            version=payload.expected_version + 1,
            reviewed_by=context.actor_id,
            reviewed_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "证据已被其他复核人处理")
    audit(db, context, f"pilot_evidence.{payload.decision}", "pilot_evidence", row.id, {"gate_id": row.gate_id})
    db.commit()
    db.refresh(row)
    return evidence_view(row, digest)
