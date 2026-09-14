from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .domain import (
    build_self_test_items,
    service_versions,
    test_detail,
    utcnow,
    validate_service_settings,
)
from .job_queue import LeaseHeartbeat, claim_job, clear_job_lease
from .models import (
    AgentMessage,
    AgentProposal,
    AgentRun,
    AgentSession,
    AsyncJob,
    AuditEvent,
    ConnectionTest,
    IntegrationState,
    SelfTestReport,
    ServiceConfig,
)
from .runtime_adapters import run_runtime_turn, test_agent_runtime
from .security import RequestContext

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def job_dict(job: AsyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "queue_name": job.queue_name,
        "priority": job.priority,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "idempotency_key": job.idempotency_key,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "available_at": job.available_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "lease_owner": job.lease_owner,
        "lease_expires_at": job.lease_expires_at,
        "heartbeat_at": job.heartbeat_at,
        "recovery_count": job.recovery_count,
        "cancel_requested_at": job.cancel_requested_at,
    }


def enqueue_job(
    db: Session,
    context: RequestContext,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str | None,
    max_attempts: int = 3,
    *,
    commit: bool = True,
) -> tuple[AsyncJob, bool]:
    if idempotency_key:
        existing = db.scalar(
            select(AsyncJob).where(
                AsyncJob.tenant_id == context.tenant_id,
                AsyncJob.kind == kind,
                AsyncJob.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
    job = AsyncJob(
        tenant_id=context.tenant_id,
        kind=kind,
        status="queued",
        queue_name="default",
        priority=100,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        created_by=context.actor_id,
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            action="job.queued",
            resource_type="async_job",
            resource_id=job.id,
            detail={"kind": kind, "idempotency_key": idempotency_key},
        )
    )
    if commit:
        db.commit()
        db.refresh(job)
    return job, True


def _connection_test(db: Session, job: AsyncJob) -> dict[str, Any]:
    service_type = str(job.payload.get("service_type") or "")
    config = db.scalar(
        select(ServiceConfig).where(ServiceConfig.tenant_id == job.tenant_id, ServiceConfig.service_type == service_type)
    )
    if not config:
        raise ValueError("请先保存服务配置")
    validate_service_settings(service_type, config.settings)
    if not config.secret_ref:
        raise ValueError("服务凭证尚未托管")
    model_config = db.scalar(
        select(ServiceConfig).where(
            ServiceConfig.tenant_id == job.tenant_id,
            ServiceConfig.service_type == "model",
        )
    ) if service_type == "agent" else None
    latency, detail = test_agent_runtime(config, model_config) if service_type == "agent" else test_detail(service_type)
    tested_at = utcnow()
    result = ConnectionTest(
        tenant_id=job.tenant_id,
        service_type=service_type,
        config_version=config.version,
        status="passed",
        latency_ms=latency,
        detail=detail,
        created_at=tested_at,
    )
    config.connected = True
    config.latency_ms = latency
    config.connection_tested_at = tested_at
    db.add(result)
    db.flush()
    return {
        "connection_test_id": result.id,
        "service_type": service_type,
        "config_version": config.version,
        "status": "passed",
        "latency_ms": latency,
        "detail": detail,
        "created_at": tested_at.isoformat(),
    }


def _self_test(db: Session, job: AsyncJob) -> dict[str, Any]:
    configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == job.tenant_id)))
    items = build_self_test_items(configs)
    passed = len(configs) == 4 and all(item["status"] == "passed" for item in items)
    now = utcnow()
    report = SelfTestReport(
        tenant_id=job.tenant_id,
        status="passed" if passed else "blocked",
        service_versions=service_versions(configs),
        items=items,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(report)
    db.flush()
    state = db.get(IntegrationState, job.tenant_id) or IntegrationState(tenant_id=job.tenant_id)
    db.add(state)
    state.last_self_test_id = report.id
    state.enabled = False
    state.enabled_at = None
    state.enabled_by = None
    state.enabled_service_versions = None
    state.invalidated_reason = None if passed else "沙箱自测存在未通过项"
    return {
        "report_id": report.id,
        "status": report.status,
        "service_versions": report.service_versions,
        "items": items,
        "created_at": now.isoformat(),
        "expires_at": report.expires_at.isoformat(),
    }


def _agent_turn(db: Session, job: AsyncJob) -> dict[str, Any]:
    session_id = str(job.payload.get("session_id") or "")
    content = str(job.payload.get("content") or "").strip()
    session = db.scalar(
        select(AgentSession).where(AgentSession.id == session_id, AgentSession.tenant_id == job.tenant_id)
    )
    if not session:
        raise ValueError("Agent 会话不存在或不属于当前租户")
    if not content:
        raise ValueError("消息内容不能为空")

    run = AgentRun(
        tenant_id=job.tenant_id,
        session_id=session.id,
        job_id=job.id,
        provider=session.runtime_provider,
        profile=session.runtime_profile,
        status="running",
        tool_trace=[],
        evidence=[],
    )
    db.add(run)
    db.flush()
    # Persist the running record before crossing the provider/process boundary.
    # A Runtime crash must remain visible even when the surrounding turn
    # transaction is rolled back.
    db.commit()
    db.refresh(run)
    generated, checkpoint, runtime = run_runtime_turn(db, session, content)
    answer = generated["answer"]
    assistant_message = AgentMessage(
        tenant_id=job.tenant_id,
        session_id=session.id,
        role="assistant",
        content=answer["body"],
        structured=answer,
    )
    db.add(assistant_message)
    db.flush()

    proposal_out = None
    if generated.get("proposal"):
        spec = generated["proposal"]
        proposal = AgentProposal(
            tenant_id=job.tenant_id,
            session_id=session.id,
            message_id=assistant_message.id,
            action_type=spec["action_type"],
            arguments=spec["arguments"],
            status="pending",
            required_role=spec["required_role"],
            expires_at=utcnow() + timedelta(minutes=30),
        )
        db.add(proposal)
        db.flush()
        proposal_out = {
            "id": proposal.id,
            "action_type": proposal.action_type,
            "arguments": proposal.arguments,
            "status": proposal.status,
            "required_role": proposal.required_role,
            "expires_at": proposal.expires_at.isoformat(),
        }
        answer = {**answer, "proposal": proposal_out}
        assistant_message.structured = answer

    run.status = "succeeded"
    run.tool_trace = generated["tool_trace"]
    run.evidence = generated["evidence"]
    run.completed_at = utcnow()
    checkpoint.last_run_id = run.id
    session.updated_at = utcnow()
    return {
        "session_id": session.id,
        "message_id": assistant_message.id,
        "run_id": run.id,
        "provider": run.provider,
        "profile": run.profile,
        "answer": answer,
        "tool_trace": run.tool_trace,
        "evidence": run.evidence,
        "proposal": proposal_out,
        "runtime": runtime,
    }


JOB_HANDLERS = {
    "integration.connection_test": _connection_test,
    "integration.self_test": _self_test,
    "agent.turn": _agent_turn,
}


def execute_claimed_job(session_factory: sessionmaker, job_id: str, worker_id: str) -> str | None:
    """Execute a job only while the caller owns its active lease."""

    with LeaseHeartbeat(session_factory, job_id, worker_id), session_factory() as db:
        job = db.scalar(
            select(AsyncJob).where(
                AsyncJob.id == job_id,
                AsyncJob.status == "running",
                AsyncJob.lease_owner == worker_id,
            )
        )
        if not job:
            return None
        claimed_attempt = job.attempt
        try:
            handler = JOB_HANDLERS.get(job.kind)
            if not handler:
                raise ValueError(f"未知作业类型：{job.kind}")
            result = handler(db, job)
            db.expire(job)
            job = db.scalar(
                select(AsyncJob).where(
                    AsyncJob.id == job_id,
                    AsyncJob.status == "running",
                    AsyncJob.lease_owner == worker_id,
                    AsyncJob.lease_expires_at > utcnow(),
                    AsyncJob.attempt == claimed_attempt,
                )
            )
            if not job:
                return "lease_lost"
            if job.cancel_requested_at:
                job.status = "cancelled"
                job.result = None
                job.error = "作业在当前安全边界完成后取消"
                job.completed_at = utcnow()
                clear_job_lease(job)
                db.add(
                    AuditEvent(
                        tenant_id=job.tenant_id,
                        actor_id=job.created_by,
                        action="job.cancelled",
                        resource_type="async_job",
                        resource_id=job.id,
                        detail={"kind": job.kind, "attempt": job.attempt, "mode": "cooperative"},
                    )
                )
                db.commit()
                return job.status
            job.status = "succeeded"
            job.result = result
            job.error = None
            job.completed_at = utcnow()
            clear_job_lease(job)
            db.add(
                AuditEvent(
                    tenant_id=job.tenant_id,
                    actor_id=job.created_by,
                    action="job.succeeded",
                    resource_type="async_job",
                    resource_id=job.id,
                    detail={"kind": job.kind, "attempt": job.attempt, "worker_id": worker_id},
                )
            )
            db.commit()
            return job.status
        # This is the durable job boundary: every handler failure must be
        # persisted as a failed job and, when applicable, a failed AgentRun.
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.scalar(
                select(AsyncJob).where(
                    AsyncJob.id == job_id,
                    AsyncJob.status == "running",
                    AsyncJob.lease_owner == worker_id,
                    AsyncJob.lease_expires_at > utcnow(),
                    AsyncJob.attempt == claimed_attempt,
                )
            )
            if not job:
                return "lease_lost"
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            cancelled = bool(job.cancel_requested_at)
            job.status = "cancelled" if cancelled else "failed"
            job.error = "作业执行期间收到取消请求" if cancelled else str(message)[:2000]
            job.completed_at = utcnow()
            clear_job_lease(job)
            run = db.scalar(select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.status == "running"))
            if run:
                run.status = "failed"
                run.error = job.error
                run.completed_at = job.completed_at
            db.add(
                AuditEvent(
                    tenant_id=job.tenant_id,
                    actor_id=job.created_by,
                    action="job.cancelled" if cancelled else "job.failed",
                    resource_type="async_job",
                    resource_id=job.id,
                    detail={"kind": job.kind, "attempt": job.attempt, "error": job.error, "worker_id": worker_id},
                )
            )
            db.commit()
            return job.status


def execute_job(session_factory: sessionmaker, job_id: str) -> None:
    """Backward-compatible inline dispatcher used by local development/tests."""

    worker_id = f"api-inline:{job_id}"
    claimed_id = claim_job(session_factory, worker_id, job_id=job_id)
    if claimed_id:
        execute_claimed_job(session_factory, claimed_id, worker_id)
