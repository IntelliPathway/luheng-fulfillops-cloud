from __future__ import annotations

import os
import threading
from datetime import timedelta
from typing import Any, Self

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from .domain import utcnow
from .job_broker import broker_health
from .models import AgentRun, AsyncJob, AuditEvent, JobWorker
from .version import APP_VERSION

DEFAULT_QUEUE = "default"
WORKER_VERSION = APP_VERSION


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def job_execution_mode() -> str:
    mode = os.getenv("JOB_EXECUTION_MODE", "inline").strip().lower()
    if mode not in {"inline", "external"}:
        raise RuntimeError("JOB_EXECUTION_MODE 只能是 inline 或 external")
    return mode


def configured_lease_seconds() -> int:
    return _bounded_int("JOB_LEASE_SECONDS", 60, 15, 900)


def configured_worker_stale_seconds() -> int:
    return _bounded_int("WORKER_STALE_SECONDS", 30, 10, 300)


def should_execute_inline() -> bool:
    return job_execution_mode() == "inline"


def _claimable_jobs(queue_names: tuple[str, ...], now: Any):
    return (
        select(AsyncJob.id)
        .where(
            AsyncJob.status == "queued",
            AsyncJob.queue_name.in_(queue_names),
            AsyncJob.attempt < AsyncJob.max_attempts,
            or_(AsyncJob.available_at.is_(None), AsyncJob.available_at <= now),
        )
        .order_by(AsyncJob.priority.asc(), AsyncJob.created_at.asc())
    )


def claim_job(
    session_factory: sessionmaker,
    worker_id: str,
    *,
    queue_names: tuple[str, ...] = (DEFAULT_QUEUE,),
    lease_seconds: int | None = None,
    job_id: str | None = None,
) -> str | None:
    """Atomically lease one queued job.

    PostgreSQL workers use SKIP LOCKED. SQLite retains a conditional UPDATE so
    a second worker cannot claim the same candidate during local tests.
    """

    now = utcnow()
    lease_seconds = lease_seconds or configured_lease_seconds()
    with session_factory() as db:
        statement = _claimable_jobs(queue_names, now)
        if job_id:
            statement = statement.where(AsyncJob.id == job_id)
        statement = statement.limit(1)
        if db.bind and db.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        candidate_id = db.scalar(statement)
        if not candidate_id:
            return None
        claimed = db.execute(
            update(AsyncJob)
            .where(
                AsyncJob.id == candidate_id,
                AsyncJob.status == "queued",
                AsyncJob.attempt < AsyncJob.max_attempts,
                or_(AsyncJob.available_at.is_(None), AsyncJob.available_at <= now),
            )
            .values(
                status="running",
                attempt=AsyncJob.attempt + 1,
                started_at=now,
                completed_at=None,
                error=None,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
                cancel_requested_at=None,
            )
        )
        if claimed.rowcount != 1:
            db.rollback()
            return None
        job = db.get(AsyncJob, candidate_id)
        db.add(
            AuditEvent(
                tenant_id=job.tenant_id,
                actor_id=f"worker:{worker_id}",
                action="job.claimed",
                resource_type="async_job",
                resource_id=job.id,
                detail={"kind": job.kind, "attempt": job.attempt, "queue": job.queue_name},
            )
        )
        db.commit()
        return candidate_id


def heartbeat_job(
    session_factory: sessionmaker,
    job_id: str,
    worker_id: str,
    lease_seconds: int | None = None,
) -> bool:
    now = utcnow()
    lease_seconds = lease_seconds or configured_lease_seconds()
    with session_factory() as db:
        renewed = db.execute(
            update(AsyncJob)
            .where(
                AsyncJob.id == job_id,
                AsyncJob.status == "running",
                AsyncJob.lease_owner == worker_id,
                AsyncJob.lease_expires_at > now,
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        )
        db.commit()
        return renewed.rowcount == 1


def heartbeat_worker(session_factory: sessionmaker, worker_id: str, current_job_id: str) -> None:
    with session_factory() as db:
        db.execute(
            update(JobWorker)
            .where(JobWorker.id == worker_id, JobWorker.status == "running")
            .values(heartbeat_at=utcnow(), current_job_id=current_job_id)
        )
        db.commit()


def clear_job_lease(job: AsyncJob) -> None:
    job.lease_owner = None
    job.lease_expires_at = None


def recover_stale_jobs(session_factory: sessionmaker, recovered_by: str, *, limit: int = 100) -> int:
    now = utcnow()
    recovered = 0
    with session_factory() as db:
        statement = (
            select(AsyncJob)
            .where(
                AsyncJob.status == "running",
                AsyncJob.lease_expires_at.is_not(None),
                AsyncJob.lease_expires_at <= now,
            )
            .order_by(AsyncJob.lease_expires_at.asc())
            .limit(limit)
        )
        if db.bind and db.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        for job in db.scalars(statement):
            previous_owner = job.lease_owner
            previous_expiry = job.lease_expires_at
            recovery_count = (job.recovery_count or 0) + 1
            if job.cancel_requested_at:
                next_status = "cancelled"
                error = "Worker 租约过期后完成取消"
                action = "job.cancelled_after_lease_expiry"
            elif job.attempt < job.max_attempts:
                next_status = "queued"
                error = "上一次 Worker 租约过期，作业已自动重新排队"
                action = "job.recovered"
            else:
                next_status = "failed"
                error = "Worker 租约过期且已达到最大尝试次数"
                action = "job.lease_exhausted"
            recovered_row = db.execute(
                update(AsyncJob)
                .where(
                    AsyncJob.id == job.id,
                    AsyncJob.status == "running",
                    AsyncJob.lease_owner == previous_owner,
                    AsyncJob.lease_expires_at == previous_expiry,
                )
                .values(
                    status=next_status,
                    error=error,
                    recovery_count=AsyncJob.recovery_count + 1,
                    available_at=now if next_status == "queued" else job.available_at,
                    started_at=None if next_status == "queued" else job.started_at,
                    completed_at=now if next_status in {"cancelled", "failed"} else None,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            if recovered_row.rowcount != 1:
                continue
            for run in db.scalars(select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.status == "running")):
                run.status = "failed"
                run.error = "Worker 租约过期，运行已由队列恢复"
                run.completed_at = now
            db.add(
                AuditEvent(
                    tenant_id=job.tenant_id,
                    actor_id=f"worker:{recovered_by}",
                    action=action,
                    resource_type="async_job",
                    resource_id=job.id,
                    detail={
                        "kind": job.kind,
                        "attempt": job.attempt,
                        "previous_owner": previous_owner,
                        "lease_expires_at": previous_expiry.isoformat() if previous_expiry else None,
                        "recovery_count": recovery_count,
                    },
                )
            )
            recovered += 1
        db.commit()
    return recovered


def update_worker(
    session_factory: sessionmaker,
    worker_id: str,
    *,
    status: str,
    queues: tuple[str, ...] = (DEFAULT_QUEUE,),
    current_job_id: str | None = None,
    completed_status: str | None = None,
    last_error: str | None = None,
) -> None:
    now = utcnow()
    with session_factory() as db:
        worker = db.get(JobWorker, worker_id)
        if not worker:
            worker = JobWorker(id=worker_id, started_at=now)
            db.add(worker)
        worker.status = status
        worker.queues = list(queues)
        worker.current_job_id = current_job_id
        worker.version = WORKER_VERSION
        worker.heartbeat_at = now
        worker.stopped_at = now if status == "stopped" else None
        if completed_status:
            worker.processed_count = (worker.processed_count or 0) + 1
            if completed_status == "failed":
                worker.failed_count = (worker.failed_count or 0) + 1
        worker.last_error = last_error
        db.commit()


def queue_health(db: Session, tenant_id: str) -> dict[str, Any]:
    now = utcnow()
    stale_cutoff = now - timedelta(seconds=configured_worker_stale_seconds())
    tenant_jobs = AsyncJob.tenant_id == tenant_id
    queued_jobs = db.scalar(select(func.count(AsyncJob.id)).where(tenant_jobs, AsyncJob.status == "queued")) or 0
    running_jobs = db.scalar(select(func.count(AsyncJob.id)).where(tenant_jobs, AsyncJob.status == "running")) or 0
    stale_jobs = (
        db.scalar(
            select(func.count(AsyncJob.id)).where(
                tenant_jobs,
                AsyncJob.status == "running",
                AsyncJob.lease_expires_at.is_not(None),
                AsyncJob.lease_expires_at <= now,
            )
        )
        or 0
    )
    active_workers = (
        db.scalar(
            select(func.count(JobWorker.id)).where(
                JobWorker.status.in_(("starting", "idle", "running")),
                JobWorker.heartbeat_at >= stale_cutoff,
            )
        )
        or 0
    )
    latest_heartbeat = db.scalar(select(func.max(JobWorker.heartbeat_at)))
    mode = job_execution_mode()
    return {
        "mode": mode,
        "status": "inline" if mode == "inline" else "healthy" if active_workers else "degraded",
        "active_workers": active_workers,
        "queued_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "stale_jobs": stale_jobs,
        "latest_heartbeat_at": latest_heartbeat,
        "lease_seconds": configured_lease_seconds(),
        **broker_health(db),
    }


class LeaseHeartbeat:
    def __init__(
        self,
        session_factory: sessionmaker,
        job_id: str,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds or configured_lease_seconds()
        self.interval = max(
            1.0,
            min(20.0, self.lease_seconds / 3, configured_worker_stale_seconds() / 2),
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lease-{job_id}", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not heartbeat_job(self.session_factory, self.job_id, self.worker_id, self.lease_seconds):
                return
            heartbeat_worker(self.session_factory, self.worker_id, self.job_id)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
