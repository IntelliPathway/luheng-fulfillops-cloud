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
