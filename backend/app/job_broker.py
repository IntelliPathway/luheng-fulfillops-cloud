from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import AsyncJob

DATABASE_BROKER = "database"
POSTGRES_NOTIFY_BROKER = "postgres-notify"
SUPPORTED_BROKERS = {DATABASE_BROKER, POSTGRES_NOTIFY_BROKER}


class JobBrokerError(RuntimeError):
    pass


def job_broker_backend() -> str:
    backend = os.getenv("JOB_BROKER_BACKEND", DATABASE_BROKER).strip().lower()
    if backend not in SUPPORTED_BROKERS:
        raise JobBrokerError(f"JOB_BROKER_BACKEND 不支持：{backend}")
    return backend


def broker_channel() -> str:
    channel = os.getenv("JOB_BROKER_CHANNEL", "luheng_jobs").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", channel):
        raise JobBrokerError("JOB_BROKER_CHANNEL 必须是最长 63 位的 SQL 标识符")
    return channel


def publish_job_notification(db: Session, job: AsyncJob) -> None:
    if job_broker_backend() == DATABASE_BROKER:
        return
    if not db.bind or db.bind.dialect.name != "postgresql":
        raise JobBrokerError("postgres-notify 只能与 PostgreSQL DATABASE_URL 配合使用")
    payload = json.dumps({"job_id": job.id, "queue": job.queue_name}, separators=(",", ":"))
    db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": broker_channel(), "payload": payload})


def broker_health(db: Session) -> dict[str, str]:
    backend = job_broker_backend()
    if backend == DATABASE_BROKER:
        return {"broker_backend": backend, "broker_status": "polling"}
    status = "ready" if db.bind and db.bind.dialect.name == "postgresql" else "degraded"
    return {"broker_backend": backend, "broker_status": status}


class JobWakeupBroker(Protocol):
    def wait(self, stop_event: threading.Event, timeout: float) -> bool: ...

    def close(self) -> None: ...


@dataclass
class DatabasePollingBroker:
    def wait(self, stop_event: threading.Event, timeout: float) -> bool:
        return stop_event.wait(timeout)

    def close(self) -> None:
        return None


class PostgresNotifyBroker:
    def __init__(self, database_url: str) -> None:
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise JobBrokerError("postgres-notify 只能与 PostgreSQL DATABASE_URL 配合使用")
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.channel = broker_channel()
        self._connection = None

    def _connect(self):
        if self._connection is None or self._connection.closed:
            import psycopg
            from psycopg import sql

            self._connection = psycopg.connect(self.database_url, autocommit=True)
            self._connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.channel)))
        return self._connection

    def start(self) -> None:
        self._connect()

    def wait(self, stop_event: threading.Event, timeout: float) -> bool:
        if stop_event.is_set():
            return False
        try:
            connection = self._connect()
            return next(connection.notifies(timeout=timeout, stop_after=1), None) is not None
        except Exception:  # noqa: BLE001
            self.close()
            stop_event.wait(min(timeout, 1.0))
            return False

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def build_job_broker(database_url: str) -> JobWakeupBroker:
    if job_broker_backend() == POSTGRES_NOTIFY_BROKER:
        return PostgresNotifyBroker(database_url)
    return DatabasePollingBroker()
