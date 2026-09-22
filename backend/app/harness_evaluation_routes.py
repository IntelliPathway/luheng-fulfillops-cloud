from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select

from .dependencies import Context, Database
from .models import ModelReplayRun
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


@router.get("/catalog")
def harness_catalog(context: Context) -> dict:
    require_role(context, "viewer", "operator", "admin")
    return {"tenant_id": context.tenant_id, "adapters": ADAPTERS}


@router.get("/leaderboard")
def harness_leaderboard(context: Context, db: Database) -> dict:
    require_role(context, "viewer", "operator", "admin")
    rows = list(
        db.scalars(
            select(ModelReplayRun)
            .where(ModelReplayRun.tenant_id == context.tenant_id, ModelReplayRun.status.in_(("completed", "failed")))
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
        successful_runs = sum(run.status == "completed" for run in runs)
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
    return {
        "tenant_id": context.tenant_id,
        "weights": {"quality": 0.7, "reliability": 0.2, "cost_efficiency": 0.1},
        "recommendation": results[0] if results else None,
        "results": results,
        "minimum_runs_for_production": 3,
    }
