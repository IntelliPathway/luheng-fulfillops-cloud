"""Versioned customer acceptance records with fresh evidence and independent external-record review."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .case_validation import build_case_validation
from .customer_materials import find
from .dependencies import Context, Database
from .material_associations import association_view
from .models import CaseAcceptance, MaterialAssociation, utcnow
from .pilot_acceptance import configuration_digest
from .security import require_role

router = APIRouter(prefix="/api/v1/case-acceptances", tags=["case-acceptances"])


class AcceptanceCreate(BaseModel):
    association_id: str = Field(min_length=1, max_length=40)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    external_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,159}$")
    external_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validity_days: int = Field(default=30, ge=1, le=90)
    acknowledged: bool


class AcceptanceDecision(BaseModel):
    decision: Literal["accept", "reject"]
    expected_version: int = Field(ge=1)
    decision_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,159}$")
    acknowledged: bool


def snapshot(db, tenant_id: str, association_id: str, startup) -> dict:
    association = db.scalar(
        select(MaterialAssociation).where(
            MaterialAssociation.tenant_id == tenant_id, MaterialAssociation.id == association_id
        )
    )
    if association is None:
        raise HTTPException(404, "关联不存在")
    link = association_view(db, association, startup)
    case = build_case_validation(db, tenant_id, association.case_id, startup)
    checks = [
        {"id": "association", "label": "材料关联独立复核", "passed": link["effective_status"] == "approved"},
        {"id": "amounts", "label": "付款与退款分别一致", "passed": link["claim_status"] == "matched"},
        *case["checks"],
    ]
    basis = {
        "tenant_id": tenant_id,
        "association_id": association_id,
        "association_version": association.version,
        "association_evidence_digest": link["evidence_digest"],
        "effective_status": link["effective_status"],
        "case_report_digest": case["report_digest"],
        "configuration_digest": configuration_digest(db, tenant_id, startup),
        "checks": checks,
    }
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
    return {
        "tenant_id": tenant_id,
        "association_id": association_id,
        "material_id": association.material_id,
        "case_id": association.case_id,
        "evidence_digest": digest,
        "checks": checks,
        "ready_for_acceptance": all(c["passed"] for c in checks),
        "enables_external_execution": False,
    }


def acceptance_binding(current_digest: str, row: CaseAcceptance) -> str:
    statement = [
        current_digest,
        row.association_id,
        row.external_reference,
        row.external_digest,
        row.proposed_by,
        row.expires_at.isoformat(),
    ]
    return hashlib.sha256(json.dumps(statement).encode()).hexdigest()


def acceptance_view(db, row: CaseAcceptance, startup) -> dict:
    current = snapshot(db, row.tenant_id, row.association_id, startup)
    effective = row.status
    if row.status != "rejected":
        if row.expires_at <= utcnow():
            effective = "expired"
        elif row.evidence_digest != acceptance_binding(current["evidence_digest"], row):
            effective = "stale"
    record = {
        f: getattr(row, f)
        for f in (
            "id",
            "tenant_id",
            "association_id",
            "external_reference",
            "external_digest",
            "status",
            "version",
            "proposed_by",
            "reviewed_by",
            "decision_reference",
            "created_at",
            "reviewed_at",
            "expires_at",
            "evidence_digest",
        )
    }
    result = record | {
        "effective_status": effective,
        "current_evidence": current,
        "externally_attested": effective == "accepted",
        "attestation_method": "independent_admin_record_review" if effective == "accepted" else None,
        "real_business_verified": False,
        "enables_external_execution": False,
    }
    result["report_digest"] = hashlib.sha256(json.dumps(result, default=str, sort_keys=True).encode()).hexdigest()
    return result


@router.get("")
def list_acceptances(context: Context, db: Database, request: Request, response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    rows = db.scalars(
        select(CaseAcceptance)
        .where(CaseAcceptance.tenant_id == context.tenant_id)
        .order_by(CaseAcceptance.created_at.desc(), CaseAcceptance.id.desc())
        .limit(50)
    )
    return [acceptance_view(db, row, request.app.state.startup) for row in rows]


@router.get("/preflight/{association_id}")
def acceptance_preflight(
    association_id: str, context: Context, db: Database, request: Request, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return snapshot(db, context.tenant_id, association_id, request.app.state.startup)


@router.get("/{acceptance_id}/report")
def get_acceptance_report(
    acceptance_id: str, context: Context, db: Database, request: Request, response: Response
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    row = db.scalar(
        select(CaseAcceptance).where(CaseAcceptance.id == acceptance_id, CaseAcceptance.tenant_id == context.tenant_id)
    )
    if row is None:
        raise HTTPException(404, "验收记录不存在")
    return acceptance_view(db, row, request.app.state.startup)


@router.post("", status_code=201)
def propose_acceptance(payload: AcceptanceCreate, context: Context, db: Database, request: Request) -> dict:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "请确认外部验收记录已经授权、脱敏且摘要与原件一致")
    request_digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    previous = db.scalar(
        select(CaseAcceptance).where(
            CaseAcceptance.tenant_id == context.tenant_id, CaseAcceptance.idempotency_key == payload.idempotency_key
        )
    )
    if previous:
        if previous.request_digest != request_digest:
            raise HTTPException(409, "同一验收请求编号不能对应不同内容")
        return acceptance_view(db, previous, request.app.state.startup)
    current = snapshot(db, context.tenant_id, payload.association_id, request.app.state.startup)
    if not current["checks"][0]["passed"]:
        raise HTTPException(409, "请先完成当前材料关联的独立复核")
    row = CaseAcceptance(
        tenant_id=context.tenant_id,
        association_id=payload.association_id,
        idempotency_key=payload.idempotency_key,
        request_digest=request_digest,
        evidence_digest=current["evidence_digest"],
        external_reference=payload.external_reference,
        external_digest=payload.external_digest,
        proposed_by=context.actor_id,
        expires_at=utcnow() + timedelta(days=payload.validity_days),
    )
    row.evidence_digest = acceptance_binding(current["evidence_digest"], row)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "验收请求已被创建，请读取现有记录") from None
    audit(
        db,
        context,
        "case_acceptance.proposed",
        "case_acceptance",
        row.id,
        {"digest": row.evidence_digest, "external_digest": row.external_digest},
    )
    db.commit()
    return acceptance_view(db, row, request.app.state.startup)


@router.post("/{acceptance_id}/decision")
def decide_acceptance(
    acceptance_id: str, payload: AcceptanceDecision, context: Context, db: Database, request: Request
) -> dict:
    require_role(context, "admin")
    row = db.scalar(
        select(CaseAcceptance)
        .where(CaseAcceptance.id == acceptance_id, CaseAcceptance.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "验收记录不存在")
    association = db.scalar(
        select(MaterialAssociation).where(
            MaterialAssociation.id == row.association_id, MaterialAssociation.tenant_id == context.tenant_id
        )
    )
    if association is None:
        raise HTTPException(409, "材料关联失效")
    material = find(db, context.tenant_id, association.material_id)
    if context.actor_id in {row.proposed_by, association.proposed_by, material.created_by}:
        raise HTTPException(409, "验收复核人必须独立于申请人、关联提案人与材料提交人")
    if not payload.acknowledged or row.status != "pending_review" or row.version != payload.expected_version:
        raise HTTPException(409, "验收记录已变化或未确认独立核验")
    current = snapshot(db, context.tenant_id, row.association_id, request.app.state.startup)
    if payload.decision == "accept" and (
        row.expires_at <= utcnow()
        or row.evidence_digest != acceptance_binding(current["evidence_digest"], row)
        or not current["ready_for_acceptance"]
    ):
        raise HTTPException(409, "证据已变化、过期或验收条件未满足，请重新核对")
    result = db.execute(
        update(CaseAcceptance)
        .where(
            CaseAcceptance.id == row.id,
            CaseAcceptance.status == "pending_review",
            CaseAcceptance.version == payload.expected_version,
        )
        .values(
            status="accepted" if payload.decision == "accept" else "rejected",
            version=payload.expected_version + 1,
            reviewed_by=context.actor_id,
            reviewed_at=utcnow(),
            decision_reference=payload.decision_reference,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "验收已被其他人处理")
    audit(
        db,
        context,
        f"case_acceptance.{payload.decision}",
        "case_acceptance",
        row.id,
        {
            "version": payload.expected_version + 1,
            "reference": payload.decision_reference,
            "digest": row.evidence_digest,
        },
    )
    db.commit()
    db.refresh(row)
    return acceptance_view(db, row, request.app.state.startup)
