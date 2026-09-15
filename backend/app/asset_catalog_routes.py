from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from .asset_catalog import AssetCatalogError, get_case, list_cases, list_packages
from .dependencies import Context, Database
from .schemas import AssetPackagePageOut, CaseCatalogItemOut, CaseCatalogPageOut

router = APIRouter(prefix="/api/v1", tags=["asset-catalog"])


@router.get("/asset-packages", response_model=AssetPackagePageOut)
def get_asset_packages(
    context: Context,
    db: Database,
    query: str | None = Query(default=None, min_length=1, max_length=80),
    policy_status: Literal["draft", "published"] | None = None,
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AssetPackagePageOut:
    return AssetPackagePageOut(
        **list_packages(
            db,
            context.tenant_id,
            query=query,
            policy_status=policy_status,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/cases", response_model=CaseCatalogPageOut)
def get_cases(
    context: Context,
    db: Database,
    query: str | None = Query(default=None, min_length=1, max_length=80),
    package_id: str | None = Query(default=None, min_length=1, max_length=40),
    status: str | None = Query(default=None, min_length=1, max_length=40),
    view: Literal["all", "signed", "blocked", "quality"] = "all",
    sort: Literal["case_id", "created_desc", "balance_desc"] = "case_id",
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> CaseCatalogPageOut:
    return CaseCatalogPageOut(
        **list_cases(
            db,
            context.tenant_id,
            query=query,
            package_id=package_id,
            status=status,
            view=view,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/cases/{case_id}", response_model=CaseCatalogItemOut)
def get_case_detail(case_id: str, context: Context, db: Database) -> CaseCatalogItemOut:
    try:
        return CaseCatalogItemOut(**get_case(db, context.tenant_id, case_id))
    except AssetCatalogError as exc:
        raise HTTPException(status_code=exc.http_status, detail=f"{exc}（{exc.code}）") from exc
