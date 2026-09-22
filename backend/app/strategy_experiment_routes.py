from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .models import AssetPackage, CaseRecord, RecoveryLedgerEntry, StrategyExperiment, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/strategy-experiments", tags=["strategy-experiments"])


class ExperimentCreate(BaseModel):
    package_id: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=8, max_length=1000)
    candidate_policy_version: int = Field(ge=1)
    allocation_bps: int = Field(ge=100, le=5000)
    success_metric: Literal["confirmed_recovery_rate"] = "confirmed_recovery_rate"
    acknowledged: bool


class ExperimentTransition(BaseModel):
    action: Literal["start", "stop"]
    expected_version: int = Field(ge=1)
    acknowledged: bool


def _view(row: StrategyExperiment) -> dict:
    return {
        "id": row.id,
        "package_id": row.package_id,
        "name": row.name,
        "hypothesis": row.hypothesis,
        "control_policy_version": row.control_policy_version,
        "candidate_policy_version": row.candidate_policy_version,
        "allocation_bps": row.allocation_bps,
        "success_metric": row.success_metric,
        "status": row.status,
        "version": row.version,
        "created_by": row.created_by,
        "started_by": row.started_by,
        "stopped_by": row.stopped_by,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "stopped_at": row.stopped_at,
    }


def _assigned(experiment_id: str, case_id: str, allocation_bps: int) -> str:
    bucket = int(hashlib.sha256(f"{experiment_id}:{case_id}".encode()).hexdigest()[:8], 16) % 10000
    return "candidate" if bucket < allocation_bps else "control"


@router.get("")
def list_experiments(context: Context, db: Database) -> list[dict]:
    rows = db.scalars(
        select(StrategyExperiment)
        .where(StrategyExperiment.tenant_id == context.tenant_id)
        .order_by(StrategyExperiment.created_at.desc())
    ).all()
    return [_view(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentCreate, context: Context, db: Database) -> dict:
    require_role(context, "operator", "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认实验创建后仍需另一名管理员启动")
    package = db.scalar(
        select(AssetPackage).where(
            AssetPackage.tenant_id == context.tenant_id,
            AssetPackage.package_id == payload.package_id,
        )
    )
    if package is None or package.policy_status != "published":
        raise HTTPException(404, "未找到已发布策略的资产包")
    if payload.candidate_policy_version == package.policy_version:
        raise HTTPException(422, "候选版本必须与当前控制版本不同")
    row = StrategyExperiment(
        tenant_id=context.tenant_id,
        package_id=package.package_id,
        name=payload.name,
        hypothesis=payload.hypothesis,
        control_policy_version=package.policy_version,
        candidate_policy_version=payload.candidate_policy_version,
        allocation_bps=payload.allocation_bps,
        success_metric=payload.success_metric,
        created_by=context.actor_id,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        context,
        "strategy_experiment.created",
        "strategy_experiment",
        row.id,
        {"allocation_bps": row.allocation_bps},
    )
    db.commit()
    return _view(row)


@router.post("/{experiment_id}/transition")
def transition_experiment(experiment_id: str, payload: ExperimentTransition, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认已核验实验范围、指标与保护边界")
    row = db.scalar(
        select(StrategyExperiment).where(
            StrategyExperiment.id == experiment_id,
            StrategyExperiment.tenant_id == context.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(404, "实验不存在")
    if row.version != payload.expected_version:
        raise HTTPException(409, "实验版本已变化，请刷新后重试")
    if payload.action == "start":
        if row.status != "draft":
            raise HTTPException(409, "仅草稿实验可启动")
        if row.created_by == context.actor_id:
            raise HTTPException(409, "创建者不能启动自己的实验")
        row.status, row.started_by, row.started_at = "running", context.actor_id, utcnow()
    else:
        if row.status != "running":
            raise HTTPException(409, "仅运行中实验可停止")
        row.status, row.stopped_by, row.stopped_at = "stopped", context.actor_id, utcnow()
    row.version += 1
    audit(db, context, f"strategy_experiment.{row.status}", "strategy_experiment", row.id, {"version": row.version})
    db.commit()
    return _view(row)


@router.get("/{experiment_id}/results")
def experiment_results(experiment_id: str, context: Context, db: Database) -> dict:
    row = db.scalar(
        select(StrategyExperiment).where(
            StrategyExperiment.id == experiment_id,
            StrategyExperiment.tenant_id == context.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(404, "实验不存在")
    cases = db.scalars(
        select(CaseRecord).where(
            CaseRecord.tenant_id == context.tenant_id,
            CaseRecord.package_id == row.package_id,
        )
    ).all()
    recovered = set(
        db.scalars(
            select(RecoveryLedgerEntry.case_id).where(
                RecoveryLedgerEntry.tenant_id == context.tenant_id,
                RecoveryLedgerEntry.package_id == row.package_id,
                RecoveryLedgerEntry.amount_cents > 0,
            )
        ).all()
    )
    cohorts = {"control": {"cases": 0, "recovered_cases": 0}, "candidate": {"cases": 0, "recovered_cases": 0}}
    for case in cases:
        cohort = _assigned(row.id, case.case_id, row.allocation_bps)
        cohorts[cohort]["cases"] += 1
        cohorts[cohort]["recovered_cases"] += int(case.case_id in recovered)
    for values in cohorts.values():
        values["recovery_rate"] = round(values["recovered_cases"] / values["cases"], 4) if values["cases"] else None
    return {
        "experiment_id": row.id,
        "status": row.status,
        "as_of": utcnow(),
        "cohorts": cohorts,
        "assignment": "sha256-stable-v1",
    }
