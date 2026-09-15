from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Query, status

from .asset_imports import (
    AssetImportError,
    commit_import_batch,
    create_import_preview,
    list_import_batches,
)
from .audit import audit
from .dependencies import Context, Database
from .schemas import AssetImportBatchOut, AssetImportCommitRequest, AssetImportPreviewRequest
from .security import require_role

router = APIRouter(prefix="/api/v1/asset-imports", tags=["asset-imports"])


def _raise_import_error(exc: AssetImportError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc


@router.get("", response_model=list[AssetImportBatchOut])
def get_import_batches(
    context: Context,
    db: Database,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AssetImportBatchOut]:
    return [AssetImportBatchOut(**row) for row in list_import_batches(db, context.tenant_id, limit)]


@router.post("/previews", response_model=AssetImportBatchOut, status_code=status.HTTP_201_CREATED)
def preview_asset_import(
    payload: AssetImportPreviewRequest,
    context: Context,
    db: Database,
) -> AssetImportBatchOut:
    require_role(context, "operator", "admin")
    try:
        result = create_import_preview(
            db,
            context.tenant_id,
            context.actor_id,
            filename=payload.filename,
            csv_text=payload.csv_text,
            idempotency_key=payload.idempotency_key,
        )
        if not result["idempotent_replay"]:
            audit(
                db,
                context,
                "asset_import.previewed",
                "asset_import_batch",
                result["id"],
                {
                    "source_digest": result["source_digest"],
                    "status": result["status"],
                    "row_count": result["row_count"],
                    "valid_count": result["valid_count"],
                    "invalid_count": result["invalid_count"],
                    "duplicate_count": result["duplicate_count"],
                },
            )
        db.commit()
        return AssetImportBatchOut(**result)
    except AssetImportError as exc:
        db.rollback()
        _raise_import_error(exc)


@router.post("/{batch_id}/commit", response_model=AssetImportBatchOut)
def commit_asset_import(
    batch_id: str,
    payload: AssetImportCommitRequest,
    context: Context,
    db: Database,
) -> AssetImportBatchOut:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="必须明确确认导入提交")
    try:
        result = commit_import_batch(
            db,
            context.tenant_id,
            context.actor_id,
            batch_id,
            expected_version=payload.expected_version,
            review_note=payload.review_note,
        )
        if not result["idempotent_replay"]:
            audit(
                db,
                context,
                "asset_import.committed",
                "asset_import_batch",
                result["id"],
                {
                    "source_digest": result["source_digest"],
                    "valid_count": result["valid_count"],
                    "package_count": result["package_count"],
                    "total_claim_balance_cents": result["total_claim_balance_cents"],
                    "review_note_digest": hashlib.sha256(result["review_note"].encode("utf-8")).hexdigest(),
                },
            )
        db.commit()
        return AssetImportBatchOut(**result)
    except AssetImportError as exc:
        db.rollback()
        _raise_import_error(exc)
