"""Business queue association for isolated SIP echo only. No debt/media/model flow."""
import hashlib
import os
from datetime import UTC
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from .domain import utcnow
from .loan_collection import actor_is_active, authorization_blocker, lock_case, preflight
from .loan_models import LoanSession, LoanSipDispatch
from .loan_policy import lock_policy_scope, policy_for
from .sip_lab import CHANNEL_STATES, ORIGIN, LabConfig, SIPLabError


def lab_config(tenant):
    try:
        config = LabConfig.from_environment()
        if os.getenv("SIP_LAB_TENANT_ID") != tenant:
            raise SIPLabError("实验室未绑定任务租户")
        return config
    except SIPLabError as exc:
        raise HTTPException(503, "内部 SIP 测试未启用或未绑定当前租户") from exc


def dispatch_view(row):
    return {"id": row.id, "session_id": row.session_id, "state": row.state,
            "mode": "internal_sip_echo", "target": "linphone_1001",
            "audio_verified": False, "ai_dialogue_ready": False, "pstn_enabled": False,
            "customer_contact": False, "observation": row.observation}


def reserve_dispatch(db, context, session_id, expected_version):
    config = lab_config(context.tenant_id)
    lock_policy_scope(db, context.tenant_id)
    session = db.scalar(select(LoanSession).where(
        LoanSession.tenant_id == context.tenant_id, LoanSession.id == session_id,
    ))
    if not session:
        raise HTTPException(404, "会话不存在或不属于当前租户")
    lock_case(db, context.tenant_id, session.case_id)
    if session.version != expected_version:
        raise HTTPException(409, "会话版本已变化")
    existing = db.scalar(select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id, LoanSipDispatch.session_id == session_id,
    ))
    if existing:
        if existing.instance_id != config.instance:
            raise HTTPException(409, "实验室实例已变化，请核对原呼叫；不得重新派发")
        return existing, False
    reasons = execution_blockers(db, session, context.actor_id, expected_version)
    if reasons:
        raise HTTPException(409, "；".join(reasons))
    now = utcnow()
    day_start = now.replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(UTC).replace(tzinfo=None)
    tenant_count = db.scalar(select(func.count()).select_from(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id, LoanSipDispatch.created_at >= day_start,
    ))
    case_count = db.scalar(select(func.count()).select_from(LoanSipDispatch).join(LoanSession,
        (LoanSession.tenant_id == LoanSipDispatch.tenant_id) & (LoanSession.id == LoanSipDispatch.session_id),
    ).where(LoanSipDispatch.tenant_id == context.tenant_id, LoanSession.case_id == session.case_id,
            LoanSipDispatch.created_at >= day_start))
    if tenant_count >= 20 or case_count >= policy_for(db, context.tenant_id).daily_session_limit:
        raise HTTPException(409, "内部 SIP 测试日预留配额已达上限；未知及阻断结果不释放配额")
    row = LoanSipDispatch(tenant_id=context.tenant_id, session_id=session.id,
                          session_version=session.version, instance_id=config.instance,
                          authorized_by=context.actor_id)
    db.add(row)
    db.flush()
    return row, True


def execution_blockers(db, session, actor, expected_version):
    gate = preflight(db, session.tenant_id, session.case_id)
    reasons = list(gate["blockers"])
    if not actor_is_active(db, session.tenant_id, actor, {"admin"}):
        reasons.append("派发管理员权限已失效")
    blocker = authorization_blocker(db, session, utcnow())
    if blocker:
        reasons.append(blocker)
    if session.mode != "sandbox" or session.state != "identity_pending" or session.version != expected_version:
        reasons.append("会话状态或版本已变化")
    if session.profile_version != gate["profile_version"] or session.policy_version != gate["policy_version"]:
        reasons.append("政策或资料版本已变化")
    return reasons


def channel_id(row):
    return "RG-LAB-" + hashlib.sha256(f"{row.instance_id}:{row.id}".encode()).hexdigest()[:32]


def http_client(config):
    return httpx.Client(base_url=ORIGIN, auth=("repayguard_lab", config.password),
                        timeout=5, follow_redirects=False, trust_env=False)


def dispatch_job(db, job):
    # The job lease + tenant lock serializes transitions. A committed dispatching
    # intent is never sent again, even if a process crashes before the HTTP call.
    lock_policy_scope(db, job.tenant_id)
    row = db.scalar(select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == job.tenant_id, LoanSipDispatch.id == job.payload.get("dispatch_id"),
    ).execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, "派发记录不存在或不属于任务租户")
    config = lab_config(job.tenant_id)
    if config.instance != row.instance_id:
        raise HTTPException(409, "实验室实例已变化，禁止查询或重拨原请求")
    if row.state == "blocked":
        return dispatch_view(row)
    if job.payload.get("stop_only"):
        if row.state == "prepared":
            row.state = "blocked"
            row.observation = {"blockers": ["管理员停止了尚未派发的测试"], "external_request_sent": False}
            return dispatch_view(row)
        outcome = "unknown"
        with http_client(config) as client:
            try:
                response = client.delete(f"/channels/{channel_id(row)}")
                outcome = "requested" if response.status_code == 204 else "not_found_or_ended" if response.status_code == 404 else "unknown"
            except httpx.HTTPError:
                pass
        row.state = "stop_requested"
        row.observation = {"hangup_state": outcome, "retry_allowed": False}
        return dispatch_view(row)
    session = db.scalar(select(LoanSession).where(
        LoanSession.tenant_id == job.tenant_id, LoanSession.id == row.session_id,
    ))
    lock_case(db, job.tenant_id, session.case_id)
    send = row.state == "prepared" and not job.payload.get("lookup_only")
    if row.state == "prepared" and not send:
        return dispatch_view(row)
    if send:
        blockers = execution_blockers(db, session, row.authorized_by, row.session_version)
        if blockers:
            row.state = "blocked"
            row.observation = {"blockers": blockers, "external_request_sent": False}
            return dispatch_view(row)
        owner = job.lease_owner
        row.state = "dispatching"
        db.commit()  # Durable intent before I/O: a recovered job can only GET.
        lock_policy_scope(db, job.tenant_id)
        lock_case(db, job.tenant_id, session.case_id)
        db.refresh(session)
        db.refresh(job)
        blockers = execution_blockers(db, session, row.authorized_by, row.session_version)
        if job.status != "running" or job.lease_owner != owner or not job.lease_expires_at or job.lease_expires_at <= utcnow() or job.cancel_requested_at:
            blockers.append("任务租约失效或已请求取消，不发送呼叫")
        if blockers:
            row.state = "blocked"
            row.observation = {"blockers": blockers, "external_request_sent": False}
            return dispatch_view(row)
    # Hold scope locks through bounded local I/O, so policy/protection changes
    # serialize before or after this fixed-extension request; no debtor data leaves.
    observation = {"channel_state": "unknown", "retry_allowed": False}
    with http_client(config) as client:
        try:
            if send:
                response = client.post(f"/channels/{channel_id(row)}", params={
                    "endpoint": "PJSIP/1001", "context": "lab-echo", "extension": "1000",
                    "priority": 1, "callerId": "RepayGuard SIP Lab <1000>", "timeout": 20,
                })
                row.state = "unknown"
                if response.status_code in {200, 201} and response.json().get("id") == channel_id(row):
                    row.state = "submitted"
                observation["external_request_sent"] = True
            else:
                response = client.get(f"/channels/{channel_id(row)}")
                if response.status_code == 404:
                    observation["channel_state"] = "not_found_or_ended"
                elif response.status_code == 200:
                    data = response.json()
                    if data.get("id") == channel_id(row) and data.get("state") in CHANNEL_STATES:
                        observation["channel_state"] = data["state"]
        except (httpx.HTTPError, ValueError, AttributeError):
            if send:
                row.state = "unknown"
            observation["channel_state"] = "unknown"
    row.observation = observation
    return dispatch_view(row)
