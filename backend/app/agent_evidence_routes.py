from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from .dependencies import Context, Database
from .models import Activity, AgentRun, AgentSession

router = APIRouter(prefix="/api/v1/agents/activities", tags=["agents"])


@router.get("/{activity_id}/runs")
def activity_runs(activity_id: str, context: Context, db: Database, limit: int = Query(default=20, ge=1, le=50)):
    activity = db.scalar(
        select(Activity.id).where(Activity.tenant_id == context.tenant_id, Activity.activity_id == activity_id)
    )
    if activity is None:
        raise HTTPException(404, "当前租户中不存在此活动")
    query = (
        select(AgentRun)
        .join(AgentSession, AgentSession.id == AgentRun.session_id)
        .where(
            AgentRun.tenant_id == context.tenant_id,
            AgentSession.tenant_id == context.tenant_id,
            AgentSession.scope_type == "activity",
            AgentSession.scope_id == activity_id,
        )
        .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        .limit(limit)
    )
    if context.role != "admin":
        query = query.where(AgentSession.created_by == context.actor_id)
    return [
        {
            "id": row.id,
            "session_id": row.session_id,
            "job_id": row.job_id,
            "provider": row.provider,
            "profile": row.profile,
            "status": row.status,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "tool_trace": row.tool_trace,
            "evidence": row.evidence,
        }
        for row in db.scalars(query)
    ]
