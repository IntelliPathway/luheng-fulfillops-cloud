"""Immutable material versions: replacement never rewrites historical originals."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .customer_materials import MaterialCreate, find, store_material, view
from .dependencies import Context, Database
from .models import MaterialRevision, MaterialSeries
from .security import require_role

router = APIRouter(prefix="/api/v1/material-versions", tags=["material-versions"])


class RevisionCreate(BaseModel):
    expected_latest_id: str = Field(min_length=1, max_length=40)
    material: MaterialCreate


def version_state(db, tenant, ident):
    revision = db.scalar(
        select(MaterialRevision).where(MaterialRevision.tenant_id == tenant, MaterialRevision.material_id == ident)
    )
    root = revision.root_id if revision else ident
    series = db.scalar(select(MaterialSeries).where(MaterialSeries.tenant_id == tenant, MaterialSeries.root_id == root))
    return {
        "root_id": root,
        "version": revision.version if revision else 1,
        "latest_id": series.latest_id if series else root,
        "latest_version": series.version if series else 1,
        "is_latest": not series or series.latest_id == ident,
    }


@router.get("/{ident}")
def history(ident: str, context: Context, db: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    find(db, context.tenant_id, ident)
    state = version_state(db, context.tenant_id, ident)
    revisions = db.scalars(
        select(MaterialRevision)
        .where(MaterialRevision.tenant_id == context.tenant_id, MaterialRevision.root_id == state["root_id"])
        .order_by(MaterialRevision.version)
    ).all()
    return {
        "tenant_id": context.tenant_id,
        **state,
        "items": [view(find(db, context.tenant_id, state["root_id"])) | {"version": 1}]
        + [view(find(db, context.tenant_id, r.material_id)) | {"version": r.version} for r in revisions],
    }


@router.post("/{ident}", status_code=201)
def append_revision(ident: str, payload: RevisionCreate, context: Context, db: Database):
    require_role(context, "operator", "admin")
    find(db, context.tenant_id, ident)
    state = version_state(db, context.tenant_id, ident)
    series = db.scalar(
        select(MaterialSeries)
        .where(MaterialSeries.tenant_id == context.tenant_id, MaterialSeries.root_id == state["root_id"])
        .with_for_update()
    )
    if not series:
        series = MaterialSeries(
            tenant_id=context.tenant_id, root_id=state["root_id"], latest_id=state["root_id"], version=1
        )
        db.add(series)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "版本已变化，请刷新") from None
    if payload.expected_latest_id != series.latest_id or ident != series.latest_id:
        raise HTTPException(409, "仅能基于当前最新材料追加版本")
    if series.version >= 50:
        raise HTTPException(422, "单组材料最多 50 个版本")
    old_version = series.version
    old_latest = series.latest_id
    material = store_material(payload.material, context, db, commit=False)
    existing = db.scalar(
        select(MaterialRevision).where(
            MaterialRevision.tenant_id == context.tenant_id, MaterialRevision.material_id == material["id"]
        )
    )
    other_root = db.scalar(
        select(MaterialSeries).where(
            MaterialSeries.tenant_id == context.tenant_id, MaterialSeries.root_id == material["id"]
        )
    )
    if material["id"] == state["root_id"] or existing or other_root:
        raise HTTPException(409, "该原件已存在于版本历史，不重复追加")
    changed = db.execute(
        update(MaterialSeries)
        .where(
            MaterialSeries.id == series.id,
            MaterialSeries.version == old_version,
            MaterialSeries.latest_id == old_latest,
        )
        .values(latest_id=material["id"], version=old_version + 1)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "材料版本已变化")
    db.add(
        MaterialRevision(
            tenant_id=context.tenant_id,
            material_id=material["id"],
            root_id=state["root_id"],
            parent_id=old_latest,
            version=old_version + 1,
        )
    )
    audit(
        db,
        context,
        "customer_material.versioned",
        "customer_material",
        material["id"],
        {"root_id": state["root_id"], "previous_id": old_latest, "version": old_version + 1},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "材料版本冲突，请刷新") from None
    return material | version_state(db, context.tenant_id, material["id"])
