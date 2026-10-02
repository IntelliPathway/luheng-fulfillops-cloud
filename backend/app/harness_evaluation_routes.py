from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .entitlements import EntitlementError, require_capability
from .models import HarnessPlugin, ModelReplayRun, utcnow
from .security import require_role

router = APIRouter(prefix="/api/v1/harnesses", tags=["harness-evaluation"])

ADAPTERS = [
    {
        "id": "hermes",
        "name": "Hermes Agent",
        "status": "available",
        "mode": "native",
        "features": ["tool-use", "checkpoint", "approval"],
    },
    {
        "id": "deepseek-harness",
        "name": "DeepSeek Harness",
        "status": "available",
        "mode": "optional-sdk",
        "features": ["replay", "tool-use", "cost-gate"],
    },
    {
        "id": "langgraph",
        "name": "LangGraph",
        "status": "compatible",
        "mode": "contract-adapter",
        "features": ["graph", "checkpoint", "human-in-loop"],
    },
    {
        "id": "agentscope",
        "name": "AgentScope",
        "status": "planned",
        "mode": "contract-adapter",
        "features": ["multi-agent", "evaluation"],
    },
    {
        "id": "pi-agent",
        "name": "Pi Agent",
        "status": "planned",
        "mode": "contract-adapter",
        "features": ["tool-use", "lightweight"],
    },
]

ALLOWED_CAPABILITIES = {
    "approval",
    "checkpoint",
    "cost-gate",
    "evaluation",
    "graph",
    "human-in-loop",
    "lightweight",
    "multi-agent",
    "replay",
    "tool-use",
}


class PluginRegistration(BaseModel):
    plugin_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    display_name: str = Field(min_length=2, max_length=120)
    protocol: Literal["native", "python-sdk", "json-rpc", "contract-adapter"]
    entrypoint_reference: str = Field(min_length=8, max_length=255)
    capabilities: list[str] = Field(min_length=1, max_length=20)
    acknowledged: bool


class PluginAction(BaseModel):
    expected_version: int = Field(ge=1)
    acknowledged: bool


class HarnessSelectionRequest(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list, max_length=10)
    objective: Literal["balanced", "quality", "cost"] = "balanced"


def _require_selector(db: Database, tenant_id: str) -> None:
    try:
        require_capability(db, tenant_id, "harness-selector")
    except EntitlementError as exc:
        raise HTTPException(exc.http_status, f"{exc}（{exc.code}）") from exc


def _plugin_view(row: HarnessPlugin) -> dict:
    return {
        "id": row.id,
        "plugin_key": row.plugin_key,
        "display_name": row.display_name,
        "protocol": row.protocol,
        "entrypoint_reference": row.entrypoint_reference,
        "capabilities": row.capabilities,
        "status": row.status,
        "version": row.version,
        "manifest_digest": row.manifest_digest,
        "verification_digest": row.verification_digest,
        "registered_by": row.registered_by,
        "verified_by": row.verified_by,
        "approved_by": row.approved_by,
        "registered_at": row.registered_at,
        "verified_at": row.verified_at,
        "approved_at": row.approved_at,
    }


def _leaderboard_rows(db: Database, tenant_id: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(ModelReplayRun)
            .where(ModelReplayRun.tenant_id == tenant_id, ModelReplayRun.status.in_(("passed", "failed")))
            .order_by(ModelReplayRun.created_at.desc())
        )
    )
    grouped: dict[str, list[ModelReplayRun]] = defaultdict(list)
    for row in rows:
        grouped[f"{row.provider}::{row.profile}"].append(row)
    results = []
    for key, runs in grouped.items():
        total_cases = sum(run.passed_count + run.failed_count for run in runs)
        passed = sum(run.passed_count for run in runs)
        quality = passed / total_cases if total_cases else 0.0
        successful_runs = sum(run.status == "passed" for run in runs)
        reliability = successful_runs / len(runs)
        cost = sum(run.estimated_cost_usd for run in runs) / len(runs)
        cost_score = max(0.0, 1.0 - min(cost / 0.05, 1.0))
        score = round((quality * 0.7 + reliability * 0.2 + cost_score * 0.1) * 100, 2)
        provider, profile = key.split("::", 1)
        results.append(
            {
                "provider": provider,
                "profile": profile,
                "run_count": len(runs),
                "case_count": total_cases,
                "quality_percent": round(quality * 100, 2),
                "reliability_percent": round(reliability * 100, 2),
                "average_cost_usd": round(cost, 6),
                "score": score,
                "latest_run_at": runs[0].created_at,
            }
        )
    results.sort(key=lambda item: (-item["score"], item["average_cost_usd"], item["provider"]))
    return results


@router.get("/catalog")
def harness_catalog(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    plugins = db.scalars(
        select(HarnessPlugin).where(HarnessPlugin.tenant_id == context.tenant_id).order_by(HarnessPlugin.display_name)
    ).all()
    return {"tenant_id": context.tenant_id, "adapters": ADAPTERS, "plugins": [_plugin_view(row) for row in plugins]}


@router.get("/leaderboard")
def harness_leaderboard(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    results = _leaderboard_rows(db, context.tenant_id)
    return {
        "tenant_id": context.tenant_id,
        "weights": {"quality": 0.7, "reliability": 0.2, "cost_efficiency": 0.1},
        "recommendation": results[0] if results else None,
        "results": results,
        "minimum_runs_for_production": 3,
    }


@router.post("/plugins", status_code=status.HTTP_201_CREATED)
def register_plugin(payload: PluginRegistration, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    _require_selector(db, context.tenant_id)
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认注册插件不会直接获得业务工具权限")
    capabilities = sorted(set(payload.capabilities))
    unsupported = set(capabilities) - ALLOWED_CAPABILITIES
    if unsupported:
        raise HTTPException(422, f"插件声明了未授权能力：{', '.join(sorted(unsupported))}")
    existing = db.scalar(
        select(HarnessPlugin.id).where(
            HarnessPlugin.tenant_id == context.tenant_id,
            HarnessPlugin.plugin_key == payload.plugin_key,
        )
    )
    if existing:
        raise HTTPException(409, "插件键已注册")
    manifest = payload.model_dump(exclude={"acknowledged"}) | {"capabilities": capabilities}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    row = HarnessPlugin(
        tenant_id=context.tenant_id,
        registered_by=context.actor_id,
        manifest_digest=digest,
        **manifest,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        context,
        "harness_plugin.registered",
        "harness_plugin",
        row.id,
        {"plugin_key": row.plugin_key, "manifest_digest": digest},
    )
    db.commit()
    return _plugin_view(row)


@router.post("/plugins/{plugin_id}/verify")
def verify_plugin(plugin_id: str, payload: PluginAction, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    _require_selector(db, context.tenant_id)
    row = db.scalar(
        select(HarnessPlugin).where(HarnessPlugin.id == plugin_id, HarnessPlugin.tenant_id == context.tenant_id)
    )
    if not row:
        raise HTTPException(404, "Harness 插件不存在")
    if row.status != "registered" or row.version != payload.expected_version:
        raise HTTPException(409, "插件清单已变化或状态不允许验证")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认验证仅运行隔离契约测试")
    verification = {
        "manifest_digest": row.manifest_digest,
        "protocol": row.protocol,
        "capabilities": sorted(row.capabilities),
        "contract": "fulfillops-safe-v1",
    }
    row.verification_digest = hashlib.sha256(json.dumps(verification, sort_keys=True).encode()).hexdigest()
    row.status = "verified"
    row.version += 1
    row.verified_by = context.actor_id
    row.verified_at = utcnow()
    audit(
        db,
        context,
        "harness_plugin.verified",
        "harness_plugin",
        row.id,
        {"verification_digest": row.verification_digest},
    )
    db.commit()
    return _plugin_view(row)


@router.post("/plugins/{plugin_id}/approve")
def approve_plugin(plugin_id: str, payload: PluginAction, context: Context, db: Database) -> dict:
    require_role(context, "admin")
    _require_selector(db, context.tenant_id)
    row = db.scalar(
        select(HarnessPlugin).where(HarnessPlugin.id == plugin_id, HarnessPlugin.tenant_id == context.tenant_id)
    )
    if not row:
        raise HTTPException(404, "Harness 插件不存在")
    if row.status != "verified" or row.version != payload.expected_version:
        raise HTTPException(409, "插件验证证据已失效")
    if not payload.acknowledged:
        raise HTTPException(422, "必须确认已独立核验插件协议、能力和证据")
    if context.actor_id in {row.registered_by, row.verified_by}:
        raise HTTPException(409, "注册人或验证人不能批准同一插件")
    row.status = "enabled"
    row.version += 1
    row.approved_by = context.actor_id
    row.approved_at = utcnow()
    audit(
        db,
        context,
        "harness_plugin.enabled",
        "harness_plugin",
        row.id,
        {"plugin_key": row.plugin_key, "verification_digest": row.verification_digest},
    )
    db.commit()
    return _plugin_view(row)


@router.post("/select")
def select_harness(payload: HarnessSelectionRequest, context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    _require_selector(db, context.tenant_id)
    required = set(payload.required_capabilities)
    if required - ALLOWED_CAPABILITIES:
        raise HTTPException(422, "存在不受支持的 Harness 能力要求")
    candidates = [
        {"key": row["id"], "name": row["name"], "capabilities": row["features"], "source": "built-in"}
        for row in ADAPTERS
        if row["status"] in {"available", "compatible"} and required.issubset(set(row["features"]))
    ]
    plugins = db.scalars(
        select(HarnessPlugin).where(
            HarnessPlugin.tenant_id == context.tenant_id,
            HarnessPlugin.status == "enabled",
        )
    ).all()
    candidates.extend(
        {"key": row.plugin_key, "name": row.display_name, "capabilities": row.capabilities, "source": "tenant-plugin"}
        for row in plugins
        if required.issubset(set(row.capabilities))
    )
    leaderboard = _leaderboard_rows(db, context.tenant_id)
    evidence_by_provider = {row["provider"].lower(): row for row in leaderboard}
    for candidate in candidates:
        candidate["evidence"] = evidence_by_provider.get(candidate["name"].lower())
    evidenced = [row for row in candidates if row["evidence"]]
    if payload.objective == "cost":
        evidenced.sort(key=lambda row: (row["evidence"]["average_cost_usd"], -row["evidence"]["score"]))
    elif payload.objective == "quality":
        evidenced.sort(key=lambda row: (-row["evidence"]["quality_percent"], -row["evidence"]["score"]))
    else:
        evidenced.sort(key=lambda row: -row["evidence"]["score"])
    selected = evidenced[0] if evidenced else (candidates[0] if candidates else None)
    return {
        "tenant_id": context.tenant_id,
        "objective": payload.objective,
        "required_capabilities": sorted(required),
        "selection": selected,
        "candidate_count": len(candidates),
        "evidence_level": "replay" if evidenced else "catalog-only" if selected else "none",
        "production_eligible": bool(selected and selected.get("evidence") and selected["evidence"]["run_count"] >= 3),
    }
