"""Deterministic standard-loan sandbox: candidate actions never dial or write money."""
import hashlib
import json
from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .domain import utcnow
from .loan_models import LoanEvent, LoanProfile, LoanSession
from .models import AssetPackage, CaseFinancialProfile, CaseRecord, RecoveryLedgerEntry

TERMINAL = {"paused", "closed", "ptp_recorded", "ended", "paid_claimed"}
EXCEPTIONS = {"wrong_person", "dispute", "complaint", "hardship", "human_requested", "identity_failed"}


def reject(message, status=409):
    raise HTTPException(status_code=status, detail=message)


def lock_case(db, tenant, case_id):
    # First write serializes per-case admission, including the SQLite test path.
    result = db.execute(update(CaseRecord).where(
        CaseRecord.tenant_id == tenant, CaseRecord.case_id == case_id,
    ).values(version=CaseRecord.version + 1))
    if not result.rowcount:
        reject("案件不存在或不属于当前租户", 404)
    return db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant, CaseRecord.case_id == case_id))


def net_recovery(db, tenant, case_id):
    return int(db.scalar(select(func.coalesce(func.sum(RecoveryLedgerEntry.amount_cents), 0)).where(
        RecoveryLedgerEntry.tenant_id == tenant, RecoveryLedgerEntry.case_id == case_id,
    )) or 0)


def profile_for(db, tenant, case_id):
    return db.scalar(select(LoanProfile).where(LoanProfile.tenant_id == tenant, LoanProfile.case_id == case_id))


def preflight(db: Session, tenant: str, case_id: str, now=None):
    now = now or utcnow()
    case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant, CaseRecord.case_id == case_id))
    if not case:
        reject("案件不存在或不属于当前租户", 404)
    profile = profile_for(db, tenant, case_id)
    financial = db.scalar(select(CaseFinancialProfile).where(
        CaseFinancialProfile.tenant_id == tenant, CaseFinancialProfile.case_id == case_id,
    ))
    package = db.scalar(select(AssetPackage).where(
        AssetPackage.tenant_id == tenant, AssetPackage.package_id == case.package_id,
    ))
    reasons = []
    if case.blocked or case.status in {"已结清", "停止联系", "本期已足额"}:
        reasons.append("案件保护或终态")
    if not case.contact_basis_ref:
        reasons.append("缺少联系依据")
    if not financial or not (financial.mandate_start <= now.date() <= financial.mandate_end):
        reasons.append("委托无效或缺失")
    if not package or package.policy_status != "published":
        reasons.append("资产政策未发布")
    amount = None
    if not profile:
        reasons.append("缺少标准贷款资料")
    else:
        dpd = (now.replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).date() - profile.due_date).days
        if not 1 <= dpd <= 30:
            reasons.append("不属于首期 DPD 1–30 准入范围")
        if now - profile.snapshot_at > timedelta(hours=24) or profile.snapshot_at > now:
            reasons.append("账务快照过期或时间无效")
        amount = max(0, profile.amount_cents - (net_recovery(db, tenant, case_id) - profile.ledger_baseline))
        if amount <= 0:
            reasons.append("没有待履约金额")
        if financial and amount > financial.claim_balance_cents:
            reasons.append("待履约金额超过现有债权金额")
    return {"case_id": case_id, "sandbox_eligible": not reasons, "blockers": reasons,
            "amount_cents": amount, "profile_version": profile.version if profile else None,
            "source_status": "operator_supplied_snapshot", "production_ready": False,
            "production_blockers": ["尚无真实电话派发与媒体适配器", "机构本人核验方式待接入"]}


def session_view(db, row):
    events = list(db.scalars(select(LoanEvent).where(
        LoanEvent.tenant_id == row.tenant_id, LoanEvent.session_id == row.id,
    ).order_by(LoanEvent.created_at, LoanEvent.id)))
    return {"id": row.id, "case_id": row.case_id, "mode": row.mode, "state": row.state,
            "version": row.version, "profile_version": row.profile_version,
            "created_at": row.created_at, "promise": row.promise,
            "events": [{"intent": e.intent, "created_at": e.created_at, "result": e.result} for e in events]}


def start_session(db, tenant, actor, payload):
    lock_case(db, tenant, payload.case_id)
    existing = db.scalar(select(LoanSession).where(
        LoanSession.tenant_id == tenant, LoanSession.request_key == payload.request_key,
    ))
    if existing:
        if existing.case_id != payload.case_id or existing.mode != payload.mode:
            reject("任务幂等键与原请求不一致")
        return existing
    if payload.mode != "sandbox":
        reject("真实电话适配器尚未接入，不允许回退为沙箱", 503)
    gate = preflight(db, tenant, payload.case_id)
    if not gate["sandbox_eligible"]:
        reject("；".join(gate["blockers"]))
    # Conservative sandbox cadence; never reserves or consumes real contact attempts.
    now = utcnow()
    recent = list(db.scalars(select(LoanSession).where(
        LoanSession.tenant_id == tenant, LoanSession.case_id == payload.case_id,
    )))
    if any(r.state not in {"paused", "closed", "ended"} and (
        r.state != "ptp_recorded" or r.promise.get("status") in {"pending", "partial"}
    ) for r in recent):
        reject("存在在途会话、待核实到账或待履约承诺")
    day_start = now.replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).astimezone(UTC).replace(tzinfo=None)
    if sum(r.created_at >= day_start for r in recent) >= 3:
        reject("沙箱案件当日任务上限为 3 次")
    row = LoanSession(tenant_id=tenant, case_id=payload.case_id, request_key=payload.request_key,
                      mode="sandbox", profile_version=gate["profile_version"], authorized_by=actor)
    db.add(row)
    db.flush()
    return row


def record_event(db, tenant, session_id, payload):
    row = db.scalar(select(LoanSession).where(LoanSession.tenant_id == tenant, LoanSession.id == session_id))
    if not row:
        reject("会话不存在", 404)
    lock_case(db, tenant, row.case_id)
    db.refresh(row)
    content = payload.model_dump(mode="json", exclude={"expected_version", "acknowledged"})
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(LoanEvent).where(LoanEvent.tenant_id == tenant,
        LoanEvent.session_id == row.id, LoanEvent.event_key == payload.event_key))
    if existing:
        if existing.payload_digest != digest:
            reject("事件幂等键与原载荷不一致")
        return row
    if row.version != payload.expected_version:
        reject("会话版本已变化，请刷新")
    if row.mode != "sandbox" or row.state in TERMINAL:
        reject("会话不可继续提交事件")
    gate = preflight(db, tenant, row.case_id)
    stale = gate["profile_version"] != row.profile_version
    if not gate["sandbox_eligible"] or stale:
        row.state = "paused"
        result = {"action": "pause", "reason": "；".join(gate["blockers"]) or "资料版本变化"}
    elif payload.intent in EXCEPTIONS:
        row.state = "paused"
        result = {"action": "handoff", "reason": payload.intent,
                  "detail": "沙箱异常已暂停；未改变真实案件保护状态"}
    elif payload.intent == "identity_verified" and row.state == "identity_pending":
        row.state = "debt_explained"
        result = {"action": "explain_debt", "amount_cents": gate["amount_cents"],
                  "verification": "sandbox_assertion", "detail": "模拟本人核验，不代表真实身份验证"}
    elif payload.intent == "promise" and row.state == "debt_explained":
        local_today = utcnow().replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).date()
        if not payload.confirmed or not payload.amount_cents or not payload.due_date:
            reject("承诺必须明确确认金额和日期", 422)
        if payload.amount_cents > gate["amount_cents"] or not local_today <= payload.due_date <= local_today + timedelta(days=30):
            reject("承诺金额或日期超出首期范围", 422)
        row.promise = {"amount_cents": payload.amount_cents, "due_date": payload.due_date.isoformat(),
                       "timezone": "Asia/Shanghai", "status": "pending", "confirmed": True,
                       "ledger_baseline": net_recovery(db, tenant, row.case_id), "paid_cents": 0}
        row.state = "ptp_recorded"
        result = {"action": "track_promise", "detail": "仅记录还款承诺，不改变合同或账务"}
    elif payload.intent == "paid_claimed" and row.state == "debt_explained":
        row.state = "paid_claimed"
        result = {"action": "verify_payment", "detail": "自述已还款待核实，不能据此结清"}
    elif payload.intent == "end":
        row.state = "ended"
        result = {"action": "end", "detail": "会话结束"}
    else:
        reject("当前对话阶段不允许该动作", 422)
    row.version += 1
    db.add(LoanEvent(tenant_id=tenant, session_id=row.id, event_key=payload.event_key,
                    payload_digest=digest, intent=payload.intent, result=result))
    db.flush()
    return row


def reconcile(db, tenant, session_id):
    row = db.scalar(select(LoanSession).where(LoanSession.tenant_id == tenant, LoanSession.id == session_id))
    if not row:
        reject("会话不存在", 404)
    lock_case(db, tenant, row.case_id)
    db.refresh(row)
    if not row.promise:
        reject("没有还款承诺")
    profile = profile_for(db, tenant, row.case_id)
    if not profile or profile.version != row.profile_version:
        reject("资料已变化，需核实后重新记录承诺")
    paid = max(0, net_recovery(db, tenant, row.case_id) - row.promise["ledger_baseline"])
    today = utcnow().replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    status = "fulfilled" if paid >= row.promise["amount_cents"] else "broken" if today > row.promise["due_date"] else "partial" if paid else "pending"
    promise = {**row.promise, "paid_cents": paid, "status": status}
    if promise != row.promise:
        row.promise = promise
        row.version += 1
    # A fulfilled PTP does not mean the debt is settled. Never change CaseRecord or ledger.
    db.flush()
    return row


def promise_check_job(db, job):
    from .models import Tenant, TenantLifecycle, TenantMembership, User
    tenant = db.get(Tenant, job.tenant_id)
    user = db.get(User, job.created_by)
    lifecycle = db.get(TenantLifecycle, job.tenant_id)
    if lifecycle and lifecycle.stage in {"suspended", "closed"}:
        reject("租户暂停或已关闭", 403)
    member = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == job.tenant_id, TenantMembership.user_id == job.created_by,
    ))
    if not tenant or not user or user.status != "active" or not member or member.status != "active" or member.role not in {"admin", "operator"}:
        reject("到期核验提交人的当前权限已失效", 403)
    row = reconcile(db, job.tenant_id, str(job.payload.get("session_id") or ""))
    return {"session_id": row.id, "mode": row.mode, "promise_status": row.promise["status"],
            "paid_cents": row.promise["paid_cents"]}
