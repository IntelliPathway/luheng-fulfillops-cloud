"""Evidence suggestions and independent association review; never repair receipt or ledger facts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .case_validation import build_case_validation
from .customer_materials import build_material_report, find
from .dependencies import Context, Database
from .models import MaterialAssociation, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/material-associations", tags=["material-associations"])


class LinkCreate(BaseModel):
    material_id: str = Field(min_length=1, max_length=40)
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
    acknowledged: bool


class LinkDecision(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)
    decision_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,159}$")
    acknowledged: bool


def association_snapshot(db, tenant_id: str, material_id: str, case_id: str, startup) -> dict:
    report = build_material_report(db, tenant_id, material_id, startup)
    case = build_case_validation(db, tenant_id, case_id, startup)
    claim = next((r for r in report["results"] if r["case_id"] == case_id), None)
    if report["material"]["file_kind"] == "csv" and claim is None:
        raise HTTPException(422, "CSV 没有该案件的声明，不能关联")
    from .material_versions import version_state

    revision = version_state(db, tenant_id, material_id)
    basis = {
        "material_version": revision,
        "tenant_id": tenant_id,
        "material_id": material_id,
        "source_digest": report["material"]["source_digest"],
        "mapping": report["material"]["mapping"],
        "source_reference": report["material"]["source_reference"],
        "case_id": case_id,
        "case_report_digest": case["report_digest"],
        "claim": claim,
    }
    digest = hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "evidence_digest": digest,
        "claim_status": "superseded_material"
        if not revision["is_latest"]
        else claim["status"]
        if claim
        else "attachment_only",
        "checks": case["checks"],
    }


def association_view(db, row: MaterialAssociation, startup) -> dict:
    current = association_snapshot(db, row.tenant_id, row.material_id, row.case_id, startup)
    return {
        f: getattr(row, f)
        for f in (
            "id",
            "tenant_id",
            "material_id",
            "case_id",
            "evidence_digest",
            "status",
            "version",
            "proposed_by",
            "reviewed_by",
            "decision_reference",
            "created_at",
            "reviewed_at",
        )
    } | {
        "effective_status": "stale" if current["evidence_digest"] != row.evidence_digest else row.status,
        "claim_status": current["claim_status"],
        "externally_attested": False,
        "enables_external_execution": False,
    }


@router.get("")
def list_associations(context: Context, db: Database, request: Request, response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    rows = db.scalars(
        select(MaterialAssociation)
        .where(MaterialAssociation.tenant_id == context.tenant_id)
        .order_by(MaterialAssociation.created_at.desc(), MaterialAssociation.id.desc())
        .limit(50)
    )
    return [association_view(db, row, request.app.state.startup) for row in rows]


@router.get("/suggestions/{material_id}")
def suggestions(material_id: str, context: Context, db: Database, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    report = build_material_report(db, context.tenant_id, material_id, request.app.state.startup)
    return {
        "tenant_id": context.tenant_id,
        "material_id": material_id,
        "suggestions": [
            {"case_id": r["case_id"], "status": r["status"], "can_propose": r["status"] != "case_not_found"}
            for r in report["results"]
        ],
        "automatic_approval": False,
    }


@router.post("", status_code=201)
def propose_association(payload: LinkCreate, context: Context, db: Database, request: Request) -> dict:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "请确认仅创建关联提案，不修改账务或验签结论")
    from .material_versions import version_state

    if not version_state(db, context.tenant_id, payload.material_id)["is_latest"]:
        raise HTTPException(409, "材料已被新版本替代，请选择最新材料")
    snapshot = association_snapshot(
        db, context.tenant_id, payload.material_id, payload.case_id, request.app.state.startup
    )
    row = db.scalar(
        select(MaterialAssociation)
        .where(
            MaterialAssociation.tenant_id == context.tenant_id,
            MaterialAssociation.material_id == payload.material_id,
            MaterialAssociation.case_id == payload.case_id,
        )
        .with_for_update()
    )
    if row and row.status != "rejected" and row.evidence_digest == snapshot["evidence_digest"]:
        return association_view(db, row, request.app.state.startup)
    if row:
        old_version = row.version
        result = db.execute(
            update(MaterialAssociation)
            .where(MaterialAssociation.id == row.id, MaterialAssociation.version == old_version)
            .values(
                version=old_version + 1,
                status="pending_review",
                evidence_digest=snapshot["evidence_digest"],
                proposed_by=context.actor_id,
                reviewed_by=None,
                reviewed_at=None,
                decision_reference=None,
                created_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            raise HTTPException(409, "关联已变化，请刷新")
        db.refresh(row)
    else:
        row = MaterialAssociation(
            tenant_id=context.tenant_id,
            material_id=payload.material_id,
            case_id=payload.case_id,
            evidence_digest=snapshot["evidence_digest"],
            proposed_by=context.actor_id,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "关联已由其他操作创建，请刷新") from None
    audit(
        db,
        context,
        "material_association.proposed",
        "material_association",
        row.id,
        {"version": row.version, "digest": row.evidence_digest},
    )
    db.commit()
    return association_view(db, row, request.app.state.startup)


@router.post("/{association_id}/decision")
def decide_association(
    association_id: str, payload: LinkDecision, context: Context, db: Database, request: Request
) -> dict:
    require_role(context, "admin")
    row = db.scalar(
        select(MaterialAssociation)
        .where(MaterialAssociation.id == association_id, MaterialAssociation.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "关联不存在")
    material = find(db, context.tenant_id, row.material_id)
    if context.actor_id in {row.proposed_by, material.created_by}:
        raise HTTPException(409, "复核人必须独立于提案人与材料提交人")
    if not payload.acknowledged or row.status != "pending_review" or row.version != payload.expected_version:
        raise HTTPException(409, "请确认复核，或刷新已变化的提案")
    current = association_snapshot(db, context.tenant_id, row.material_id, row.case_id, request.app.state.startup)
    if current["evidence_digest"] != row.evidence_digest:
        raise HTTPException(409, "案件或材料证据已变化，请重新提案")
    changed = db.execute(
        update(MaterialAssociation)
        .where(
            MaterialAssociation.id == row.id,
            MaterialAssociation.version == payload.expected_version,
            MaterialAssociation.status == "pending_review",
        )
        .values(
            status="approved" if payload.decision == "approve" else "rejected",
            version=payload.expected_version + 1,
            reviewed_by=context.actor_id,
            decision_reference=payload.decision_reference,
            reviewed_at=utcnow(),
        )
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "关联已被处理")
    audit(
        db,
        context,
        f"material_association.{payload.decision}",
        "material_association",
        row.id,
        {
            "version": payload.expected_version + 1,
            "reference": payload.decision_reference,
            "digest": row.evidence_digest,
        },
    )
    db.commit()
    db.refresh(row)
    return association_view(db, row, request.app.state.startup)
