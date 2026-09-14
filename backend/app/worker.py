from __future__ import annotations

import argparse
import os
import signal
import threading
from datetime import timedelta
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from .db import build_engine, build_session_factory
from .domain import utcnow
from .harness_runtime import close_harness_runtimes
from .job_queue import (
    DEFAULT_QUEUE,
    claim_job,
    configured_lease_seconds,
    configured_worker_stale_seconds,
    recover_stale_jobs,
    update_worker,
)
from .jobs import execute_claimed_job
from .models import JobWorker


def _worker_id() -> str:
    return os.getenv("WORKER_ID", "").strip() or f"worker-{uuid4().hex[:12]}"


def _poll_interval() -> float:
    try:
        value = float(os.getenv("WORKER_POLL_SECONDS", "1"))
    except ValueError as exc:
        raise RuntimeError("WORKER_POLL_SECONDS 必须是数字") from exc
    if not 0.1 <= value <= 60:
        raise RuntimeError("WORKER_POLL_SECONDS 必须在 0.1 到 60 之间")
    return value


class DatabaseWorker:
    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        worker_id: str,
        queues: tuple[str, ...] = (DEFAULT_QUEUE,),
        lease_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.queues = queues
        self.lease_seconds = lease_seconds or configured_lease_seconds()

    def run_once(self) -> bool:
        recover_stale_jobs(self.session_factory, self.worker_id)
        update_worker(self.session_factory, self.worker_id, status="idle", queues=self.queues)
        job_id = claim_job(
            self.session_factory,
            self.worker_id,
            queue_names=self.queues,
            lease_seconds=self.lease_seconds,
        )
        if not job_id:
            return False
        update_worker(
            self.session_factory,
            self.worker_id,
            status="running",
            queues=self.queues,
            current_job_id=job_id,
        )
        completed_status = execute_claimed_job(self.session_factory, job_id, self.worker_id)
        last_error = {
            "succeeded": None,
            "cancelled": None,
            "lease_lost": "作业租约已被回收；旧 Worker 的终态写入已阻止",
        }.get(completed_status, "作业执行失败")
        update_worker(
            self.session_factory,
            self.worker_id,
            status="idle",
            queues=self.queues,
            completed_status=completed_status,
            last_error=last_error,
        )
        return True

    def run_forever(self, stop_event: threading.Event, poll_seconds: float) -> None:
        update_worker(self.session_factory, self.worker_id, status="starting", queues=self.queues)
        try:
            while not stop_event.is_set():
                handled = self.run_once()
                if not handled:
                    stop_event.wait(poll_seconds)
        finally:
            update_worker(self.session_factory, self.worker_id, status="stopped", queues=self.queues)
            close_harness_runtimes()


def worker_is_healthy(session_factory: sessionmaker, worker_id: str) -> bool:
    with session_factory() as db:
        worker = db.get(JobWorker, worker_id)
        return bool(
            worker
            and worker.status in {"starting", "idle", "running"}
            and worker.heartbeat_at >= utcnow() - timedelta(seconds=configured_worker_stale_seconds())
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="履衡 AI 持久作业 Worker")
    parser.add_argument("--once", action="store_true", help="最多处理一个作业后退出")
    parser.add_argument("--healthcheck", action="store_true", help="检查当前 WORKER_ID 心跳")
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./luheng-dev.db")
    session_factory = build_session_factory(build_engine(database_url))
    worker_id = _worker_id()
    if args.healthcheck:
        return 0 if worker_is_healthy(session_factory, worker_id) else 1

    queues = tuple(item.strip() for item in os.getenv("WORKER_QUEUES", DEFAULT_QUEUE).split(",") if item.strip())
    if not queues:
        raise RuntimeError("WORKER_QUEUES 至少需要一个队列")
    worker = DatabaseWorker(session_factory, worker_id=worker_id, queues=queues)
    if args.once:
        update_worker(session_factory, worker_id, status="starting", queues=queues)
        try:
            worker.run_once()
        finally:
            update_worker(session_factory, worker_id, status="stopped", queues=queues)
            close_harness_runtimes()
        return 0

    stop_event = threading.Event()

    def stop_worker(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    worker.run_forever(stop_event, _poll_interval())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
