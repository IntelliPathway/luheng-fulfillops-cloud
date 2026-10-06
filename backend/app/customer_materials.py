"""Encrypted customer evidence intake. Imported claims never write financial ledgers."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .case_validation import build_case_validation
from .dependencies import Context, Database
from .models import CustomerMaterial
from .secret_store import SecretStoreError, _keyring
from .security import require_role

router = APIRouter(prefix="/api/v1/customer-materials", tags=["customer-materials"])
MAX_BYTES = 1_000_000
FIELDS = {"case_id", "payment_cents", "refund_cents"}


class MaterialCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=120, pattern=r"^[^/\\\x00-\x1f\x7f]+$")
    source_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{3,159}$")
    file_kind: Literal["csv", "pdf", "png", "jpg"]
    content_base64: str = Field(min_length=1, max_length=1_333_336)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=3)
    acknowledged: bool


def keyring():
    try:
        return _keyring()
    except SecretStoreError as exc:
        raise HTTPException(503, "材料加密服务尚未配置，请联系管理员") from exc


def parse_rows(raw: bytes, mapping: dict) -> list[dict]:
    if set(mapping) != FIELDS or len(set(mapping.values())) != 3:
        raise HTTPException(422, "请分别映射案件编号、付款金额分、退款金额分，不能复用同一列")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader)
        if len(headers) > 30 or len(set(headers)) != len(headers) or any(not h or len(h) > 80 for h in headers):
            raise ValueError
        if not set(mapping.values()) <= set(headers):
            raise HTTPException(422, "映射列不存在于 CSV 表头")
        indexes = {f: headers.index(h) for f, h in mapping.items()}
        rows, seen = [], set()
        for number, values in enumerate(reader, 2):
            if number > 51 or len(values) != len(headers):
                raise HTTPException(422, "CSV 最多包含 50 条案件汇总，且每行列数必须一致")
            row = {f: values[i].strip() for f, i in indexes.items()}
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", row["case_id"]) or row["case_id"] in seen:
                raise HTTPException(422, f"第 {number} 行案件编号无效或重复")
            seen.add(row["case_id"])
            for f in ("payment_cents", "refund_cents"):
                if not re.fullmatch(r"0|[1-9][0-9]{0,15}", row[f]) or int(row[f]) > 9007199254740991:
                    raise HTTPException(422, f"第 {number} 行金额必须为非负整数分")
                row[f] = int(row[f])
            rows.append(row)
        if not rows:
            raise HTTPException(422, "CSV 没有案件数据")
        return rows
    except (UnicodeDecodeError, csv.Error, StopIteration, ValueError) as exc:
        raise HTTPException(422, "CSV 必须是 UTF-8 编码、具有唯一表头的有效文件") from exc


def mapping_digest(mapping: dict, source_reference: str) -> str:
    return hashlib.sha256(
        json.dumps({"mapping": mapping, "source_reference": source_reference}, sort_keys=True).encode()
    ).hexdigest()


def validate_metadata(row: CustomerMaterial) -> None:
    if mapping_digest(row.mapping, row.source_reference) != row.mapping_digest:
        raise HTTPException(503, "材料来源或映射完整性校验失败")


def material_aad(
    tenant_id: str, digest: str, md: str, filename: str, file_kind: str, size_bytes: int, case_count: int
) -> bytes:
    return json.dumps(
        ["customer-material-v1", tenant_id, digest, md, filename, file_kind, size_bytes, case_count], ensure_ascii=False
    ).encode()


def view(row: CustomerMaterial) -> dict:
    decrypt_material(row, row.tenant_id)
    return {
        f: getattr(row, f)
        for f in (
            "id",
            "tenant_id",
            "filename",
            "source_reference",
            "source_digest",
            "file_kind",
            "size_bytes",
            "mapping",
            "created_by",
            "created_at",
        )
    } | {"case_count": row.case_count, "source_authenticity": "requires_external_attestation"}


def find(db, tenant: str, material_id: str) -> CustomerMaterial:
    row = db.scalar(
        select(CustomerMaterial).where(CustomerMaterial.tenant_id == tenant, CustomerMaterial.id == material_id)
    )
    if row is None:
        raise HTTPException(404, "材料不存在")
    return row


@router.get("")
def list_materials(context: Context, db: Database, response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    rows = db.scalars(
        select(CustomerMaterial)
        .where(CustomerMaterial.tenant_id == context.tenant_id)
        .order_by(CustomerMaterial.created_at.desc(), CustomerMaterial.id.desc())
        .limit(50)
    )
    return [view(r) for r in rows]


@router.post("", status_code=201)
def create_material(payload: MaterialCreate, context: Context, db: Database) -> dict:
    return store_material(payload, context, db)


def store_material(payload: MaterialCreate, context, db, *, commit=True) -> dict:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "请确认材料已获授权并完成脱敏")
    version, keys = keyring()
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(422, "文件编码无效") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(422, "文件大小必须为 1 字节至 1 MB")
    if not payload.filename.lower().endswith("." + payload.file_kind):
        raise HTTPException(422, "文件扩展名与类型不一致")
    signatures = {"pdf": b"%PDF-", "png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff"}
    if payload.file_kind in signatures and not raw.startswith(signatures[payload.file_kind]):
        raise HTTPException(422, "附件格式无效")
    if payload.file_kind != "csv" and payload.mapping:
        raise HTTPException(422, "仅 CSV 支持字段映射；附件不会自动识别或验签")
    rows = parse_rows(raw, payload.mapping) if payload.file_kind == "csv" else []
    digest = hashlib.sha256(raw).hexdigest()
    md = mapping_digest(payload.mapping, payload.source_reference)
    existing = db.scalar(
        select(CustomerMaterial).where(
            CustomerMaterial.tenant_id == context.tenant_id,
            CustomerMaterial.source_digest == digest,
            CustomerMaterial.mapping_digest == md,
        )
    )
    if existing:
        return view(existing) | {"idempotent_replay": True}
    nonce = os.urandom(12)
    aad = material_aad(context.tenant_id, digest, md, payload.filename, payload.file_kind, len(raw), len(rows))
    row = CustomerMaterial(
        tenant_id=context.tenant_id,
        filename=payload.filename,
        source_reference=payload.source_reference,
        source_digest=digest,
        mapping_digest=md,
        file_kind=payload.file_kind,
        size_bytes=len(raw),
        key_version=version,
        nonce=base64.b64encode(nonce).decode(),
        ciphertext=base64.b64encode(AESGCM(keys[version]).encrypt(nonce, raw, aad)).decode(),
        mapping=payload.mapping,
        case_count=len(rows),
        created_by=context.actor_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(CustomerMaterial).where(
                CustomerMaterial.tenant_id == context.tenant_id,
                CustomerMaterial.source_digest == digest,
                CustomerMaterial.mapping_digest == md,
            )
        )
        if existing is None:
            raise
        return view(existing) | {"idempotent_replay": True}
    audit(
        db,
        context,
        "customer_material.created",
        "customer_material",
        row.id,
        {"digest": digest, "file_kind": row.file_kind},
    )
    if commit:
        db.commit()
    return view(row) | {"idempotent_replay": False}


def decrypt_material(row: CustomerMaterial, tenant_id: str) -> bytes:
    validate_metadata(row)
    _, keys = keyring()
    if row.key_version not in keys:
        raise HTTPException(503, "材料历史加密密钥不可用")
    try:
        aad = material_aad(
            tenant_id,
            row.source_digest,
            row.mapping_digest,
            row.filename,
            row.file_kind,
            row.size_bytes,
            row.case_count,
        )
        raw = AESGCM(keys[row.key_version]).decrypt(base64.b64decode(row.nonce), base64.b64decode(row.ciphertext), aad)
        if hashlib.sha256(raw).hexdigest() != row.source_digest:
            raise ValueError
    except Exception as exc:
        raise HTTPException(503, "材料完整性校验失败") from exc
    return raw


@router.get("/{material_id}/content")
def download_material(material_id: str, context: Context, db: Database) -> Response:
    require_role(context, "operator", "admin")
    row = find(db, context.tenant_id, material_id)
    raw = decrypt_material(row, context.tenant_id)
    audit(db, context, "customer_material.downloaded", "customer_material", row.id, {})
    db.commit()
    return Response(
        raw,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{row.id}.{row.file_kind}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{material_id}/report")
def report_material(material_id: str, context: Context, db: Database, request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return build_material_report(db, context.tenant_id, material_id, request.app.state.startup)


def build_material_report(db, tenant_id: str, material_id: str, startup) -> dict:
    row = find(db, tenant_id, material_id)
    raw = decrypt_material(row, tenant_id)
    claims = parse_rows(raw, row.mapping) if row.file_kind == "csv" else []
    results = []
    for claim in claims:
        try:
            r = build_case_validation(db, tenant_id, claim["case_id"], startup)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            results.append(
                {**claim, "status": "case_not_found", "payment_difference_cents": None, "refund_difference_cents": None}
            )
            continue
        complete = next(c["passed"] for c in r["checks"] if c["id"] == "outcome")
        pd = r["payment_total_cents"] - claim["payment_cents"] if complete else None
        rd = r["refund_total_cents"] - claim["refund_cents"] if complete else None
        results.append(
            {
                **claim,
                "status": "unavailable" if not complete else "matched" if pd == rd == 0 else "mismatch",
                "payment_difference_cents": pd,
                "refund_difference_cents": rd,
                "checks": r["checks"],
                "case_report_digest": r["report_digest"],
            }
        )
    return {
        "tenant_id": tenant_id,
        "material": view(row),
        "scope": "all_time_case_totals",
        "results": results,
        "status": "attachment_only" if not results else "compared",
        "real_business_verified": False,
        "enables_external_execution": False,
        "externally_attested": False,
    }
