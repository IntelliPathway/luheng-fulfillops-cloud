from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from .dependencies import Context, Database
from .models import Activity, AgentRun, AgentSession, CaseRecord, RecoveryLedgerEntry, utcnow

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


@router.get("/{activity_id}/summary")
def activity_summary(activity_id: str, context: Context, db: Database):
    activity = db.scalar(
        select(Activity).where(Activity.tenant_id == context.tenant_id, Activity.activity_id == activity_id)
    )
    if activity is None:
        raise HTTPException(404, "当前租户中不存在此活动")
    money = db.execute(
        select(
            func.coalesce(func.sum(RecoveryLedgerEntry.amount_cents), 0),
            func.coalesce(func.sum(RecoveryLedgerEntry.commission_cents), 0),
            func.count(RecoveryLedgerEntry.id),
        ).where(
            RecoveryLedgerEntry.tenant_id == context.tenant_id,
            RecoveryLedgerEntry.case_id.in_(activity.case_ids),
        )
    ).one()
    protected = db.scalar(
        select(func.count(CaseRecord.id)).where(
            CaseRecord.tenant_id == context.tenant_id,
            CaseRecord.case_id.in_(activity.case_ids),
            CaseRecord.blocked.is_(True),
        )
    )
    return {
        "tenant_id": context.tenant_id,
        "activity_id": activity.activity_id,
        "scope": "activity-case-all-time",
        "activity_attribution": False,
        "confirmed_net_recovery_cents": int(money[0]),
        "accrued_commission_cents": int(money[1]),
        "ledger_entry_count": money[2],
        "protected_case_count": protected or 0,
        "as_of": utcnow(),
    }
