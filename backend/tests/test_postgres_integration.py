from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy import inspect, text

from app.job_broker import PostgresNotifyBroker
from app.main import create_app
from app.worker import DatabaseWorker

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


def _apply_baseline(database_url: str) -> None:
    engine = sqlalchemy_create_engine(database_url)
    migration_root = Path(__file__).resolve().parents[1] / "migrations"
    with engine.begin() as connection:
        for version in ("001_initial.sql", "002_identity_agent_jobs.sql", "003_deepseek_harness_runtime.sql"):
            for statement in (migration_root / version).read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement.strip())
    engine.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL 未配置")
def test_v04_postgres_upgrade_notify_and_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert POSTGRES_URL is not None
    schema = f"fulfillops_{uuid4().hex[:12]}"
    admin_engine = sqlalchemy_create_engine(POSTGRES_URL)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in POSTGRES_URL else "?"
    scoped_url = f"{POSTGRES_URL}{separator}options={quote(f'-csearch_path={schema}')}"
    try:
        _apply_baseline(scoped_url)
        monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
        monkeypatch.setenv("JOB_BROKER_BACKEND", "postgres-notify")
        monkeypatch.setenv("JOB_BROKER_CHANNEL", "luheng_jobs_test")
        monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
        monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "ci-v1")
        monkeypatch.setenv("PAYMENT_SANDBOX_SECRET", "postgres-payment-signing-secret-v1")
        monkeypatch.setenv("ENABLE_PAYMENT_SANDBOX", "true")
        app = create_app(scoped_url)
        inspector = inspect(app.state.engine)
        assert "managed_secrets" in inspector.get_table_names()
        assert "model_replay_runs" in inspector.get_table_names()
        assert {
            "commission_rules",
            "case_financial_profiles",
            "payment_webhook_configs",
            "payment_receipts",
            "payment_reconciliations",
            "protection_incidents",
            "repayment_allocations",
            "repayment_installments",
            "repayment_plans",
            "recovery_ledger_entries",
            "commission_ledger_entries",
        } <= set(inspector.get_table_names())
        assert {column["name"] for column in inspector.get_columns("model_replay_runs")} >= {
            "policy_snapshot",
            "external_call_count",
            "input_tokens",
            "output_tokens",
            "estimated_cost_usd",
        }
        assert {column["name"] for column in inspector.get_columns("async_jobs")} >= {
            "lease_owner",
            "lease_expires_at",
            "heartbeat_at",
            "recovery_count",
        }
        assert {column["name"] for column in inspector.get_columns("asset_packages")} >= {
            "min_settlement_bps",
            "max_installments",
            "min_down_payment_bps",
        }
        assert "claim_balance_cents" in {
            column["name"] for column in inspector.get_columns("case_financial_profiles")
        }
        with app.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == 11

        broker = PostgresNotifyBroker(scoped_url)
        broker.start()
        headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
        with TestClient(app) as client:
            payment = client.post(
                "/api/v1/payments/sandbox-receipts",
                headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-operator"},
                json={"case_id": "C002", "amount_cents": 101_600, "idempotency_key": "postgres-payment-ci"},
            )
            assert payment.status_code == 200, payment.text
            assert payment.json()["receipt"]["status"] == "matched"
            queued = client.post(
                "/api/v1/integrations/voice/connection-test/jobs",
                headers=headers,
                json={"idempotency_key": "postgres-notify-ci"},
            )
            assert queued.status_code == 202, queued.text
            assert broker.wait(threading.Event(), 2.0) is True
            worker = DatabaseWorker(app.state.Session, worker_id="postgres-ci-worker")
            assert worker.run_once() is True
            completed = client.get(f"/api/v1/jobs/{queued.json()['id']}", headers=headers)
            assert completed.json()["status"] == "succeeded"
            queue = client.get("/api/v1/jobs/queue/health", headers=headers).json()
            assert queue["broker_backend"] == "postgres-notify"
            assert queue["broker_status"] == "ready"
        broker.close()
        app.state.engine.dispose()
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
