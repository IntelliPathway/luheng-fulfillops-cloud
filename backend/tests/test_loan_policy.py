from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from test_loan_collection import BASE, H, client, configure_policy, enroll, event, start

from app.loan_collection import preflight
from app.loan_models import LoanContactPolicy, LoanProfile, LoanSession

__all__ = ["client"]


def test_explicit_policy_required_and_isolated(client):
    enroll(client)
    with client.app.state.Session() as db:
        db.delete(db.get(LoanContactPolicy, "TENANT_A"))
        db.commit()
    assert "尚未配置" in start(client).json()["detail"]
    assert client.get(BASE + "/policy", headers=H | {"X-Tenant-ID": "TENANT_B"}).json()["policy"] is None
    assert configure_policy(client).status_code == 200
    assert start(client).status_code == 201


@pytest.mark.parametrize("change", [{"paused": True}, {"daily_session_limit": 2}])
def test_policy_changes_invalidate_old_authorization_without_rewriting_snapshot(client, change):
    enroll(client)
    row = start(client).json()
    assert row["policy_version"] == 1 and not row["policy_snapshot"]["paused"]
    assert configure_policy(client, expected_version=1, **change).status_code == 200
    result = event(client, row, "identity_verified").json()
    assert result["state"] == "paused"
    assert "amount_cents" not in result["events"][-1]["result"]
    assert result["policy_snapshot"] == row["policy_snapshot"]
    if not change.get("paused"):
        replacement = start(client, request_key="new-policy-version").json()
        assert replacement["policy_version"] == 2


def test_policy_expiry_caps_grant_and_blocks_continuation(client):
    enroll(client)
    expiry = datetime.now(UTC) + timedelta(minutes=2)
    assert configure_policy(client, expected_version=1, valid_until=expiry.isoformat()).status_code == 200
    row = start(client).json()
    assert datetime.fromisoformat(row["authorization_expires_at"]) == expiry.replace(tzinfo=None)
    with client.app.state.Session() as db:
        db.get(LoanContactPolicy, "TENANT_A").valid_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    result = event(client, row, "identity_verified").json()
    assert result["state"] == "paused" and "到期" in result["events"][-1]["result"]["reason"]


def test_policy_cadence_blocks_new_start_but_allows_admitted_dialogue(client):
    enroll(client)
    assert configure_policy(client, expected_version=1, daily_session_limit=1).status_code == 200
    row = start(client).json()
    row = event(client, row, "identity_verified").json()
    assert row["state"] == "debt_explained"
    assert event(client, row, "end").status_code == 200
    assert "当日" in start(client, request_key="second").json()["detail"]
    assert configure_policy(client, expected_version=2).status_code == 200
    assert start(client, request_key="after-limit-change").status_code == 201


@pytest.mark.parametrize("hour,minute,eligible", [(8, 59, False), (9, 0, True), (9, 59, True), (10, 0, False)])
def test_shanghai_window_has_inclusive_start_exclusive_end(client, hour, minute, eligible):
    enroll(client)
    assert configure_policy(client, expected_version=1, window_start_minute=540, window_end_minute=600).status_code == 200
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date()
    now = datetime.combine(today, time(hour, minute), ZoneInfo("Asia/Shanghai")).astimezone(UTC).replace(tzinfo=None)
    with client.app.state.Session() as db:
        profile = db.scalar(select(LoanProfile).where(LoanProfile.tenant_id == "TENANT_A", LoanProfile.case_id == "C004"))
        profile.snapshot_at = now - timedelta(minutes=1)
        profile.due_date = today - timedelta(days=3)
        db.commit()
        assert preflight(db, "TENANT_A", "C004", now)["sandbox_eligible"] is eligible


@pytest.mark.parametrize("change", [
    {"window_start_minute": 1200, "window_end_minute": 540}, {"timezone": "UTC"},
    {"daily_session_limit": 4}, {"daily_session_limit": True}, {"snapshot_max_hours": 25},
    {"promise_max_days": 31}, {"authorization_minutes": 31}, {"paused": "false"},
    {"valid_until": "2026-10-07T12:00:00"},
])
def test_policy_input_is_strict_and_conservative(client, change):
    assert configure_policy(client, expected_version=1, **change).status_code == 422


def test_policy_requires_admin_acknowledgement_and_fresh_version(client):
    assert configure_policy(client, expected_version=1, acknowledged=False).status_code == 422
    assert configure_policy(client, expected_version=0).status_code == 409
    body = client.get(BASE + "/policy", headers=H).json()["policy"]
    body = {k: v for k, v in body.items() if k not in {"tenant_id", "version", "updated_by", "updated_at", "mode", "source_status", "production_ready"}}
    body.update(valid_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(), expected_version=1, acknowledged=True)
    assert client.put(BASE + "/policy", headers=H | {"X-Actor-ID": "test-operator"}, json=body).status_code == 403
    assert configure_policy(client, expected_version=1).status_code == 200
    assert configure_policy(client, expected_version=1, paused=True).status_code == 409


def test_legacy_session_without_policy_binding_cannot_disclose(client):
    enroll(client)
    row = start(client).json()
    with client.app.state.Session() as db:
        db.get(LoanSession, row["id"]).policy_version = None
        db.commit()
    response = event(client, row, "identity_verified").json()
    assert response["state"] == "paused"
    assert "amount_cents" not in response["events"][-1]["result"]


def test_snapshot_and_promise_limits_are_current_policy(client):
    enroll(client, snapshot_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat())
    assert configure_policy(client, expected_version=1, snapshot_max_hours=1, promise_max_days=1).status_code == 200
    assert "快照" in start(client).json()["detail"]
    enroll(client, expected_version=1)
    row = event(client, start(client).json(), "identity_verified").json()
    assert event(client, row, "promise", confirmed=True, amount_cents=1000,
                 due_date=(datetime.now(UTC).date() + timedelta(days=2)).isoformat()).status_code == 422


def test_concurrent_admissions_cannot_exceed_policy_daily_limit(client, tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException

    from app.db import build_engine, build_session_factory
    from app.loan_collection import start_session
    from app.loan_routes import StartPayload

    enroll(client)
    assert configure_policy(client, expected_version=1, daily_session_limit=1).status_code == 200
    path = tmp_path / "policy-admission.db"
    with client.app.state.Session.kw["bind"].connect() as source, sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
    engine = build_engine(f"sqlite:///{path}")
    sessions = build_session_factory(engine)
    barrier = Barrier(2)

    def submit(index):
        with sessions() as db:
            barrier.wait()
            try:
                row = start_session(db, "TENANT_A", "Terry", StartPayload(case_id="C004", request_key=f"thread-{index}"))
                row.state = "ended"  # Remove the in-flight hold; the cadence must still win.
                db.commit()
                return "created"
            except HTTPException as exc:
                db.rollback()
                return exc.detail

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))
        assert results.count("created") == 1
        assert any("当日" in result for result in results)
        with sessions() as db:
            assert len(list(db.scalars(select(LoanSession)))) == 1
    finally:
        engine.dispose()
