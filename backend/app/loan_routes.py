from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import audit
from .dependencies import Context, Database
from .domain import utcnow
from .jobs import enqueue_job, job_dict
from .loan_collection import (
    lock_case,
    net_recovery,
    preflight,
    profile_for,
    reconcile,
    record_event,
    session_view,
    start_session,
)
from .loan_models import LoanContactPolicy, LoanProfile, LoanSession, LoanSipDispatch
from .loan_policy import lock_policy_scope, policy_for, policy_view
from .loan_sip_dispatch import dispatch_view, lab_config, reserve_dispatch
from .models import AsyncJob
from .security import require_role

router = APIRouter(prefix="/api/v1/loan-collection", tags=["standard-loan-collection"])
Ref = str


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledged: bool = False


class ProfilePayload(StrictPayload):
    product: str = Field(min_length=1, max_length=80)
    due_date: date
    amount_cents: int = Field(strict=True, gt=0, le=2_000_000_000)
    contact_reference: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{2,159}$")
    source_reference: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{2,159}$")
    snapshot_at: datetime
    expected_version: int = Field(ge=0)

    @field_validator("snapshot_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("快照时间必须包含时区")
        return value.astimezone(UTC).replace(tzinfo=None)


class StartPayload(StrictPayload):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    request_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    mode: Literal["sandbox", "provider"] = "sandbox"


class PolicyPayload(StrictPayload):
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    window_start_minute: int = Field(strict=True, ge=0, le=1439)
    window_end_minute: int = Field(strict=True, ge=1, le=1440)
    daily_session_limit: int = Field(strict=True, ge=1, le=3)
    snapshot_max_hours: int = Field(strict=True, ge=1, le=24)
    promise_max_days: int = Field(strict=True, ge=1, le=30)
    authorization_minutes: int = Field(strict=True, ge=1, le=30)
    paused: bool = Field(strict=True)
    authority_reference: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{2,159}$")
    valid_until: datetime
    expected_version: int = Field(strict=True, ge=0)

    @field_validator("valid_until")
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("政策到期时间必须包含时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    @model_validator(mode="after")
    def same_day_window(self):
        if self.window_start_minute >= self.window_end_minute:
            raise ValueError("联系时段必须为同日开始早于结束，不支持跨午夜")
        return self


class DispatchCheckPayload(StrictPayload):
    expected_version: int = Field(strict=True, ge=1)


class EventPayload(StrictPayload):
    event_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    expected_version: int = Field(ge=1)
    intent: Literal["identity_verified", "identity_failed", "wrong_person", "dispute", "complaint",
                    "hardship", "human_requested", "promise", "paid_claimed", "end"]
    confirmed: bool = False
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=2_000_000_000)
    due_date: date | None = None


def acknowledge(payload):
    if not payload.acknowledged:
        raise HTTPException(status_code=422, detail="必须明确确认本次操作及沙箱边界")


def finish(db, context, action, row):
    if row.state in {"paused", "closed", "ended", "ptp_recorded", "paid_claimed"}:
        queue_sip_stops(db, context, f"session-{row.version}", session_id=row.id)
    audit(db, context, action, "loan_session", row.id, {"case_id": row.case_id, "mode": row.mode})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="并发请求冲突，请刷新或使用原幂等键重试") from exc
    return session_view(db, row)


def queue_sip_stops(db, context, revision, *, session_id=None, case_id=None):
    statement = select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id,
        LoanSipDispatch.state.in_(["prepared", "dispatching", "unknown", "submitted", "stop_requested"]),
    )
    if session_id:
        statement = statement.where(LoanSipDispatch.session_id == session_id)
    if case_id:
        statement = statement.join(LoanSession, (LoanSession.id == LoanSipDispatch.session_id) &
                                   (LoanSession.tenant_id == LoanSipDispatch.tenant_id)).where(LoanSession.case_id == case_id)
    for dispatch in db.scalars(statement):
        enqueue_job(db, context, "loan.sip_echo", {"dispatch_id": dispatch.id, "stop_only": True},
                    f"sip-hold-{dispatch.id}-{revision}", commit=False)
        if dispatch.state == "prepared":
            dispatch.state = "blocked"
            dispatch.observation = {"blockers": ["资料、政策或会话状态变化，测试已停止"],
                                    "external_request_sent": False}
        elif dispatch.state != "blocked":
            dispatch.state = "stop_requested"


@router.get("/overview")
def overview(context: Context, db: Database):
    rows = list(db.scalars(select(LoanSession).where(LoanSession.tenant_id == context.tenant_id)
                          .order_by(LoanSession.created_at.desc()).limit(100)))
    dispatches = list(db.scalars(select(LoanSipDispatch).where(LoanSipDispatch.tenant_id == context.tenant_id)
                                .order_by(LoanSipDispatch.created_at.desc()).limit(100)))
    jobs = list(db.scalars(select(AsyncJob).where(AsyncJob.tenant_id == context.tenant_id,
                      AsyncJob.kind.in_(["loan.dispatch_check", "loan.sip_echo"]))
                      .order_by(AsyncJob.created_at.desc()).limit(100)))
    try:
        lab_config(context.tenant_id)
        lab_enabled = True
    except HTTPException:
        lab_enabled = False
    return {"tenant_id": context.tenant_id, "mode": "sandbox", "provider_ready": False,
            "internal_sip_echo_configured": lab_enabled,
            "sip_dispatches": [dispatch_view(row) for row in dispatches],
            "execution_jobs": [job_dict(job) for job in jobs],
            "detail": "受控状态机联调，不调用模型、不发起外呼；真实电话及本人核验适配器待接入",
            "sessions": [session_view(db, row) for row in rows]}


@router.get("/policy")
def read_policy(context: Context, db: Database):
    return {"tenant_id": context.tenant_id, "policy": policy_view(policy_for(db, context.tenant_id))}


@router.put("/policy")
def save_policy(payload: PolicyPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    now = utcnow()
    if not now < payload.valid_until <= now + timedelta(days=30):
        raise HTTPException(status_code=422, detail="政策有效期须晚于现在且不超过 30 天")
    lock_policy_scope(db, context.tenant_id)
    row = policy_for(db, context.tenant_id)
    if payload.expected_version != (row.version if row else 0):
        raise HTTPException(status_code=409, detail="政策版本已变化，请刷新")
    values = payload.model_dump(exclude={"acknowledged", "expected_version"})
    if row:
        for name, value in values.items():
            setattr(row, name, value)
        row.version += 1
        row.updated_by = context.actor_id
        row.updated_at = now
    else:
        row = LoanContactPolicy(tenant_id=context.tenant_id, updated_by=context.actor_id, **values)
        db.add(row)
    db.flush()
    audit(db, context, "loan.policy.saved", "loan_policy", context.tenant_id,
          {"version": row.version, "paused": row.paused, "mode": "sandbox"})
    queue_sip_stops(db, context, f"policy-{row.version}")
    db.commit()
    return {"tenant_id": context.tenant_id, "policy": policy_view(row)}


@router.get("/cases/{case_id}/preflight")
def check(case_id: str, context: Context, db: Database):
    return preflight(db, context.tenant_id, case_id, for_start=True)


@router.get("/cases/{case_id}/profile")
def get_profile(case_id: str, context: Context, db: Database):
    preflight(db, context.tenant_id, case_id)  # Enforce case scope even without a profile.
    row = profile_for(db, context.tenant_id, case_id)
    return None if not row else {"product": row.product, "due_date": row.due_date,
        "amount_cents": row.amount_cents, "contact_reference": row.contact_reference,
        "source_reference": row.source_reference, "snapshot_at": row.snapshot_at,
        "version": row.version, "source_status": "operator_supplied_snapshot"}


@router.put("/cases/{case_id}/profile")
def put_profile(case_id: str, payload: ProfilePayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    lock_case(db, context.tenant_id, case_id)
    row = profile_for(db, context.tenant_id, case_id)
    if payload.expected_version != (row.version if row else 0):
        raise HTTPException(status_code=409, detail="资料版本已变化，请刷新")
    values = payload.model_dump(exclude={"acknowledged", "expected_version"})
    if not row:
        row = LoanProfile(tenant_id=context.tenant_id, case_id=case_id, created_by=context.actor_id,
                          ledger_baseline=net_recovery(db, context.tenant_id, case_id), **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.ledger_baseline = net_recovery(db, context.tenant_id, case_id)
        row.version += 1
    db.flush()
    audit(db, context, "loan.profile.saved", "loan_profile", row.id,
          {"case_id": case_id, "version": row.version, "source_status": "operator_supplied_snapshot"})
    queue_sip_stops(db, context, f"profile-{row.version}", case_id=case_id)
    db.commit()
    return {"version": row.version, "preflight": preflight(db, context.tenant_id, case_id)}


@router.post("/sessions", status_code=201)
def start(payload: StartPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    row = start_session(db, context.tenant_id, context.actor_id, payload)
    return finish(db, context, "loan.session.authorized", row)


@router.post("/sessions/{session_id}/events")
def event(session_id: str, payload: EventPayload, context: Context, db: Database):
    require_role(context, "operator", "admin")
    acknowledge(payload)
    row = record_event(db, context.tenant_id, session_id, payload)
    if row.promise:
        job, created = enqueue_job(db, context, "loan.promise_check", {"session_id": row.id},
                                  f"loan-promise-{row.id}", commit=False)
        if created:
            due = date.fromisoformat(row.promise["due_date"]) + timedelta(days=1)
            job.available_at = datetime.combine(due, datetime.min.time(), ZoneInfo("Asia/Shanghai")).astimezone(UTC).replace(tzinfo=None)
    return finish(db, context, "loan.session.event", row)


@router.post("/sessions/{session_id}/reconcile")
def verify(session_id: str, payload: StrictPayload, context: Context, db: Database):
    require_role(context, "operator", "admin")
    acknowledge(payload)
    row = reconcile(db, context.tenant_id, session_id)
    return finish(db, context, "loan.promise.reconciled", row)


@router.post("/sessions/{session_id}/dispatch-check", status_code=202)
def dispatch_check(session_id: str, payload: DispatchCheckPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    lock_policy_scope(db, context.tenant_id)
    row = db.scalar(select(LoanSession).where(
        LoanSession.tenant_id == context.tenant_id, LoanSession.id == session_id,
    ))
    if not row:
        raise HTTPException(404, "会话不存在或不属于当前租户")
    lock_case(db, context.tenant_id, row.case_id)
    if row.version != payload.expected_version:
        raise HTTPException(409, "会话版本已变化，请刷新")
    job, created = enqueue_job(db, context, "loan.dispatch_check",
                              {"session_id": row.id, "session_version": row.version},
                              f"loan-dispatch-check-{row.id}-{row.version}", commit=False)
    if created:
        audit(db, context, "loan.dispatch_check.queued", "loan_session", row.id,
              {"mode": "sandbox_dispatch_check", "job_id": job.id, "external_execution": False})
    db.commit()
    return {"job": job_dict(job), "created": created, "external_execution": False,
            "detail": "持久队列执行门禁检查，不拨号、不消费真实渠道配额"}


class SipEchoPayload(DispatchCheckPayload):
    test_extension_only: bool = Field(strict=True)


@router.post("/sessions/{session_id}/sip-echo", status_code=202)
def sip_echo(session_id: str, payload: SipEchoPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    if not payload.test_extension_only:
        raise HTTPException(422, "必须明确确认只呼叫自己的 Linphone 1001 测试分机")
    row, created = reserve_dispatch(db, context, session_id, payload.expected_version)
    job, _ = enqueue_job(db, context, "loan.sip_echo", {"dispatch_id": row.id},
                         f"loan-sip-{row.id}", commit=False)
    if created:
        audit(db, context, "loan.sip_echo.reserved", "loan_sip_dispatch", row.id,
              {"mode": "internal_sip_echo", "job_id": job.id, "customer_contact": False})
    db.commit()
    return {"dispatch": dispatch_view(row), "job": job_dict(job), "created": created}


@router.get("/sip-dispatches/{dispatch_id}")
def read_sip_dispatch(dispatch_id: str, context: Context, db: Database):
    row = db.scalar(select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id, LoanSipDispatch.id == dispatch_id,
    ))
    if not row:
        raise HTTPException(404, "派发记录不存在或不属于当前租户")
    return dispatch_view(row)


class SipLookupPayload(StrictPayload):
    request_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")


@router.post("/sip-dispatches/{dispatch_id}/reconcile", status_code=202)
def reconcile_sip(dispatch_id: str, payload: SipLookupPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    lock_policy_scope(db, context.tenant_id)
    row = db.scalar(select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id, LoanSipDispatch.id == dispatch_id,
    ))
    if not row:
        raise HTTPException(404, "派发记录不存在或不属于当前租户")
    job, _ = enqueue_job(db, context, "loan.sip_echo", {"dispatch_id": row.id, "lookup_only": True},
                         f"sip-query-{row.id}-{payload.request_key}", commit=False)
    db.commit()
    return {"job": job_dict(job), "detail": "只查询原呼叫，不重拨、不续期或释放预留"}


@router.post("/sip-dispatches/{dispatch_id}/stop", status_code=202)
def stop_sip(dispatch_id: str, payload: SipLookupPayload, context: Context, db: Database):
    require_role(context, "admin")
    acknowledge(payload)
    lock_policy_scope(db, context.tenant_id)
    row = db.scalar(select(LoanSipDispatch).where(
        LoanSipDispatch.tenant_id == context.tenant_id, LoanSipDispatch.id == dispatch_id,
    ))
    if not row:
        raise HTTPException(404, "派发记录不存在或不属于当前租户")
    job, _ = enqueue_job(db, context, "loan.sip_echo", {"dispatch_id": row.id, "stop_only": True},
                         f"sip-stop-{row.id}-{payload.request_key}", commit=False)
    # Block an unsent original job immediately; a query or stop never originates.
    if row.state == "prepared":
        row.state = "blocked"
        row.observation = {"blockers": ["管理员已停止测试派发"], "external_request_sent": False}
    elif row.state != "blocked":
        row.state = "stop_requested"
    audit(db, context, "loan.sip_echo.stop_requested", "loan_sip_dispatch", row.id,
          {"mode": "internal_sip_echo", "job_id": job.id})
    db.commit()
    return {"job": job_dict(job), "dispatch": dispatch_view(row)}
