from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AuditEvent, IntegrationState, ServiceConfig


def headers(actor: str = "test-operator", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def enable_phone(client: TestClient) -> None:
    with client.app.state.Session() as db:
        phone = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "phone")
        )
        state = db.get(IntegrationState, "TENANT_A")
        assert phone and state
        phone.connected = True
        state.enabled = True
        db.commit()


def test_contact_attempt_enforces_readiness_window_frequency_and_handoff(monkeypatch) -> None:
    monkeypatch.setenv("CONTACT_MAX_ATTEMPTS_PER_CASE_DAY", "1")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        not_ready = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004",
                "scheduled_at": "2026-09-20T06:30:00Z",
                "acknowledged": True,
            },
        )
        assert not_ready.status_code == 409
        assert "phone_not_ready" in not_ready.text

        enable_phone(client)
        created = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004",
                "scheduled_at": "2026-09-20T06:30:00Z",
                "acknowledged": True,
            },
        )
        assert created.status_code == 201, created.text
        attempt = created.json()
        assert attempt["status"] == "queued"

        duplicate = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004-2",
                "scheduled_at": "2026-09-20T08:30:00Z",
                "acknowledged": True,
            },
        )
        assert duplicate.status_code == 409
        assert "daily_frequency_exceeded" in duplicate.text

        handoff = client.post(
            f"/api/v1/contact-attempts/{attempt['id']}/handoff",
            headers=headers(),
            json={"reason": "对方请求人工解释履约方案细节", "acknowledged": True},
        )
        assert handoff.status_code == 200
        assert handoff.json()["status"] == "handoff"
        assert len(client.get("/api/v1/contact-attempts", headers=headers("test-viewer")).json()) == 1
        with client.app.state.Session() as db:
            actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.resource_type == "contact_attempt")))
            assert actions == {"contact.queued", "contact.handoff"}


def test_contact_attempt_blocks_protected_case_and_outside_window() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        enable_phone(client)
        protected = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C010",
                "contact_reference": "CONTACT-REF-C010",
                "scheduled_at": "2026-09-20T06:30:00Z",
                "acknowledged": True,
            },
        )
        assert protected.status_code == 409
        outside = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004",
                "scheduled_at": datetime(2026, 9, 20, 20, 30).isoformat(),
                "acknowledged": True,
            },
        )
        assert outside.status_code == 422
