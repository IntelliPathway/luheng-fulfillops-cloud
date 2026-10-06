"""OIDC-authenticated inbound material events; encrypted input, leased workers, no ledger writes."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .customer_materials import MaterialCreate, create_material, keyring
from .dependencies import Context, Database
from .job_queue import should_execute_inline
from .jobs import enqueue_job, execute_job
from .models import AsyncJob, CustomerSyncEvent, TenantMembership, User
from .security import RequestContext, require_role

router = APIRouter(prefix="/api/v1/customer-sync", tags=["customer-sync"])


class SyncCreate(BaseModel):
    source_system: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    external_event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    material: MaterialCreate


def event_aad(row: CustomerSyncEvent) -> bytes:
    return f"customer-sync-v1:{row.tenant_id}:{row.id}:{row.source_system}:{row.external_event_id}:{row.payload_digest}:{row.created_by}:{row.job_id}".encode()


def event_view(db, row: CustomerSyncEvent) -> dict:
    job = db.get(AsyncJob, row.job_id)
    if (
        not job
        or job.tenant_id != row.tenant_id
        or job.kind != "customer.material_sync"
        or job.payload.get("sync_event_id") != row.id
    ):
        raise HTTPException(503, "同步作业关联无效")
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "source_system": row.source_system,
        "external_event_id": row.external_event_id,
        "job_id": row.job_id,
        "status": job.status,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "error": job.error,
        "material_id": (job.result or {}).get("material_id"),
        "created_at": row.created_at,
    }


@router.get("/events")
def list_events(context: Context, db: Database, response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    rows = db.scalars(
        select(CustomerSyncEvent)
        .where(CustomerSyncEvent.tenant_id == context.tenant_id)
        .order_by(CustomerSyncEvent.created_at.desc(), CustomerSyncEvent.id.desc())
        .limit(50)
    )
    return [event_view(db, row) for row in rows]


@router.post("/events", status_code=202)
@router.post("/webhook", status_code=202)
def receive_event(
    payload: SyncCreate, context: Context, db: Database, request: Request, background_tasks: BackgroundTasks
) -> dict:
    result = persist_event(payload, context, db)
    if should_execute_inline() and not result["idempotent_replay"]:
        background_tasks.add_task(execute_job, request.app.state.Session, result["job_id"])
    return result


def persist_event(payload: SyncCreate, context, db) -> dict:
    require_role(context, "operator", "admin")
    if not payload.material.acknowledged:
        raise HTTPException(422, "同步材料必须已经获得授权并脱敏")
    raw = payload.material.model_dump_json().encode()
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(
        select(CustomerSyncEvent).where(
            CustomerSyncEvent.tenant_id == context.tenant_id,
            CustomerSyncEvent.source_system == payload.source_system,
            CustomerSyncEvent.external_event_id == payload.external_event_id,
        )
    )
    if existing:
        if existing.payload_digest != digest:
            raise HTTPException(409, "同一来源事件编号不能对应不同材料")
        return event_view(db, existing) | {"idempotent_replay": True}
    version, keys = keyring()
    row = CustomerSyncEvent(
        tenant_id=context.tenant_id,
        source_system=payload.source_system,
        external_event_id=payload.external_event_id,
        payload_digest=digest,
        key_version=version,
        nonce=base64.b64encode(os.urandom(12)).decode(),
        ciphertext="",
        created_by=context.actor_id,
    )
    db.add(row)
    try:
        db.flush()
        job, _ = enqueue_job(
            db, context, "customer.material_sync", {"sync_event_id": row.id}, f"material-sync:{row.id}", commit=False
        )
        row.job_id = job.id
        row.ciphertext = base64.b64encode(
            AESGCM(keys[version]).encrypt(base64.b64decode(row.nonce), raw, event_aad(row))
        ).decode()
        audit(db, context, "customer_sync.received", "customer_sync", row.id, {"digest": digest})
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(CustomerSyncEvent).where(
                CustomerSyncEvent.tenant_id == context.tenant_id,
                CustomerSyncEvent.source_system == payload.source_system,
                CustomerSyncEvent.external_event_id == payload.external_event_id,
            )
        )
        if not existing or existing.payload_digest != digest:
            raise HTTPException(409, "同步事件冲突") from None
        return event_view(db, existing) | {"idempotent_replay": True}
    return event_view(db, row) | {"idempotent_replay": False}


def process_material_sync(db, job: AsyncJob) -> dict:
    row = db.scalar(
        select(CustomerSyncEvent).where(
            CustomerSyncEvent.id == job.payload.get("sync_event_id"), CustomerSyncEvent.tenant_id == job.tenant_id
        )
    )
    if not row or row.job_id != job.id or row.created_by != job.created_by:
        raise HTTPException(409, "同步事件与作业不匹配")
    member = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == job.tenant_id,
            TenantMembership.user_id == row.created_by,
            TenantMembership.status == "active",
        )
    )
    user = db.get(User, row.created_by)
    if not member or member.role not in {"operator", "admin"} or not user or user.status != "active":
        raise HTTPException(403, "同步提交人的当前权限已失效")
    _, keys = keyring()
    if row.key_version not in keys:
        raise HTTPException(503, "同步历史加密密钥不可用")
    try:
        raw = AESGCM(keys[row.key_version]).decrypt(
            base64.b64decode(row.nonce), base64.b64decode(row.ciphertext), event_aad(row)
        )
        if hashlib.sha256(raw).hexdigest() != row.payload_digest:
            raise ValueError
        payload = MaterialCreate.model_validate_json(raw)
    except (ValueError, Exception) as exc:
        raise HTTPException(503, "同步材料完整性校验失败") from exc
    context = RequestContext(job.tenant_id, row.created_by, member.role, user.display_name, user.email, "worker")
    result = create_material(payload, context, db)
    return {"material_id": result["id"], "sync_event_id": row.id}
