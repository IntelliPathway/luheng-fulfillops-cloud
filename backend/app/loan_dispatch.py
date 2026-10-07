"""Persistent Worker execution-gate rehearsal; never calls a phone Provider."""
from sqlalchemy import select

from .domain import utcnow
from .loan_collection import actor_is_active, authorization_blocker, lock_case, preflight
from .loan_models import LoanSession
from .loan_policy import lock_policy_scope


def dispatch_check_job(db, job):
    tenant = job.tenant_id
    lock_policy_scope(db, tenant)
    row = db.scalar(select(LoanSession).where(
        LoanSession.tenant_id == tenant, LoanSession.id == job.payload.get("session_id"),
    ))
    result = {"mode": "sandbox_dispatch_check", "external_execution": False,
              "provider_ready": False, "session_id": job.payload.get("session_id"),
              "gate_passed": False, "blockers": []}
    if not row:
        result["blockers"] = ["会话不存在或不属于任务租户"]
        return result
    lock_case(db, tenant, row.case_id)
    now = utcnow()
    gate = preflight(db, tenant, row.case_id, now)
    reasons = list(gate["blockers"])
    if not actor_is_active(db, tenant, job.created_by, {"admin"}):
        reasons.append("任务提交管理员当前权限已失效")
    blocker = authorization_blocker(db, row, now)
    if blocker:
        reasons.append(blocker)
    if row.mode != "sandbox" or row.state != "identity_pending":
        reasons.append("会话模式或状态不允许派发检查")
    if row.version != job.payload.get("session_version"):
        reasons.append("会话版本已变化")
    if row.profile_version != gate["profile_version"]:
        reasons.append("资料版本已变化")
    if row.policy_version != gate["policy_version"]:
        reasons.append("政策版本已变化")
    result.update(gate_passed=not reasons, blockers=reasons,
                  detail="只完成执行门禁检查；尚未连接真实派发、配额和媒体适配器")
    return result
