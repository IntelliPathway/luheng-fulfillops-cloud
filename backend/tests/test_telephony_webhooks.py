from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import ServiceConfig
from app.secret_store import store_secret
from app.telephony import sign_telephony_webhook


def test_signed_telephony_events_are_idempotent_and_tenant_scoped(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "telephony-test-v1")
    app = create_app("sqlite:///:memory:")
    secret = "telephony-webhook-secret"
    with app.state.Session() as db:
        config = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "phone")
        )
        assert config is not None
        config.provider = "test-sip"
        config.secret_ref, config.credential_last4 = store_secret(db, "TENANT_A", "phone", secret)
        config.connected = True
        db.commit()

    payload = {
        "event_id": "evt-call-0001",
        "call_reference": "call-safe-0001",
        "event_type": "completed",
        "occurred_at": "2026-09-19T08:00:00Z",
        "case_id": "C002",
        "activity_id": "ACT-001",
        "failure_code": None,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-RepayGuard-Timestamp": timestamp,
        "X-RepayGuard-Signature": sign_telephony_webhook(secret, timestamp, raw),
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/webhooks/telephony/TENANT_A/test-sip", content=raw, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["duplicate"] is False
        second = client.post("/api/v1/webhooks/telephony/TENANT_A/test-sip", content=raw, headers=headers)
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        listed = client.get(
            "/api/v1/telephony/events", headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
        )
        assert len(listed.json()) == 1
        assert listed.json()[0]["duplicate_count"] == 1
        other = client.get("/api/v1/telephony/events", headers={"X-Tenant-ID": "TENANT_B", "X-Actor-ID": "test-viewer"})
        assert other.json() == []


def test_invalid_telephony_signature_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        config = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "phone")
        )
        config.provider = "test-sip"
        config.secret_ref, config.credential_last4 = store_secret(db, "TENANT_A", "phone", "correct-secret")
        config.connected = True
        db.commit()
    raw = b'{"event_id":"evt-call-0002","call_reference":"call-safe-0002","event_type":"failed","occurred_at":"2026-09-19T08:00:00Z","failure_code":"provider_timeout"}'
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/telephony/TENANT_A/test-sip",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-RepayGuard-Timestamp": str(int(time.time())),
                "X-RepayGuard-Signature": "0" * 64,
            },
        )
        assert response.status_code == 401


def test_signed_callbacks_preserve_cancel_and_terminal_state(monkeypatch):
    from datetime import datetime

    from app.models import ContactAttempt

    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    app = create_app("sqlite:///:memory:")
    secret = "synthetic-callback-test-secret"
    with app.state.Session() as db:
        config = db.scalar(select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "phone"))
        config.provider = "test-sip"
        config.secret_ref, config.credential_last4 = store_secret(db, "TENANT_A", "phone", secret)
        config.connected = True
        row = ContactAttempt(tenant_id="TENANT_A", case_id="C004", contact_reference="REF-004",
                             scheduled_at=datetime(2026, 10, 7, 6), requested_by="Terry", status="blocked")
        db.add(row)
        db.commit()
        attempt_id = row.id
    def deliver(client, event_id, event_type, case_id="C004"):
        payload = {"event_id": event_id, "event_type": event_type, "call_reference": attempt_id,
                   "case_id": case_id, "activity_id": None, "occurred_at": "2026-10-07T14:00:00+08:00"}
        raw = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        return client.post("/api/v1/webhooks/telephony/TENANT_A/test-sip", content=raw,
                           headers={"X-RepayGuard-Timestamp": timestamp,
                                    "X-RepayGuard-Signature": sign_telephony_webhook(secret, timestamp, raw)})
    with TestClient(app) as client:
        assert deliver(client, "protected-completed", "completed").status_code == 200
        with app.state.Session() as db:
            assert db.get(ContactAttempt, attempt_id).status == "blocked"
            db.get(ContactAttempt, attempt_id).status = "answered"
            db.commit()
        assert deliver(client, "mismatch", "completed", "C002").status_code == 409
        assert deliver(client, "actual-completed", "completed").status_code == 200
        assert deliver(client, "late-ringing", "ringing").status_code == 200
        with app.state.Session() as db:
            assert db.get(ContactAttempt, attempt_id).status == "completed"
            assert db.get(ContactAttempt, attempt_id).last_event_at == datetime(2026, 10, 7, 6)
