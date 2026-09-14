from __future__ import annotations

import threading

import pytest

from app.job_broker import (
    DatabasePollingBroker,
    JobBrokerError,
    broker_channel,
    broker_health,
    build_job_broker,
    job_broker_backend,
    publish_job_notification,
)
from app.main import create_app
from app.models import AsyncJob


def test_database_broker_keeps_polling_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_BROKER_BACKEND", "database")
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        assert broker_health(db) == {"broker_backend": "database", "broker_status": "polling"}
    broker = build_job_broker("sqlite:///:memory:")
    assert isinstance(broker, DatabasePollingBroker)
    stop = threading.Event()
    stop.set()
    broker.wait(stop, 10)


def test_postgres_notify_rejects_non_postgres_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_BROKER_BACKEND", "postgres-notify")
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        job = AsyncJob(
            tenant_id="TENANT_A",
            kind="integration.connection_test",
            payload={},
            created_by="test-user",
        )
        db.add(job)
        db.flush()
        with pytest.raises(JobBrokerError, match="PostgreSQL"):
            publish_job_notification(db, job)
        assert broker_health(db)["broker_status"] == "degraded"
    with pytest.raises(JobBrokerError, match="PostgreSQL"):
        build_job_broker("sqlite:///jobs.db")


def test_broker_configuration_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOB_BROKER_BACKEND", "unknown")
    with pytest.raises(JobBrokerError, match="不支持"):
        job_broker_backend()
    monkeypatch.setenv("JOB_BROKER_BACKEND", "postgres-notify")
    monkeypatch.setenv("JOB_BROKER_CHANNEL", "invalid-channel;drop")
    with pytest.raises(JobBrokerError, match="SQL 标识符"):
        broker_channel()
