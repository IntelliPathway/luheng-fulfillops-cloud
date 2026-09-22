from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AuditEvent, ContactAttempt, IntegrationState, ServiceConfig


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


def test_contact_attempt_can_be_cancelled_and_failed_attempt_retried() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        enable_phone(client)
        first = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004",
                "scheduled_at": "2026-09-20T06:30:00Z",
                "acknowledged": True,
            },
        ).json()
        cancelled = client.post(
            f"/api/v1/contact-attempts/{first['id']}/cancel",
            headers=headers(),
            json={"reason": "案件状态变化，停止本次联系任务", "acknowledged": True},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "blocked"
        assert cancelled.json()["cancelled_by"] == "test-operator"

        second = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "contact_reference": "CONTACT-REF-C004-RETRY",
                "scheduled_at": "2026-09-21T06:30:00Z",
                "acknowledged": True,
            },
        ).json()
        with client.app.state.Session() as db:
            row = db.get(ContactAttempt, second["id"])
            assert row
            row.status = "failed"
            db.commit()
        retried = client.post(
            f"/api/v1/contact-attempts/{second['id']}/retry",
            headers=headers(),
            json={
                "scheduled_at": "2026-09-21T08:30:00Z",
                "reason": "线路失败后按重试预算重新排队",
                "acknowledged": True,
            },
        )
        assert retried.status_code == 201, retried.text
        assert retried.json()["retry_of_id"] == second["id"]


def test_sms_and_email_sandbox_share_frequency_and_never_require_provider_credentials(monkeypatch) -> None:
    monkeypatch.setenv("CONTACT_MAX_ATTEMPTS_PER_CASE_DAY", "1")
    monkeypatch.setenv("COMMUNICATION_SANDBOX_CHANNELS", "sms,email")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        channels = client.get("/api/v1/contact-attempts/channels", headers=headers()).json()
        assert {row["channel"]: row["mode"] for row in channels} == {
            "phone": "disabled",
            "sms": "sandbox",
            "email": "sandbox",
        }
        sms = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "channel": "sms",
                "contact_reference": "CONTACT-REF-C004-SMS",
                "scheduled_at": "2026-09-20T06:30:00Z",
                "acknowledged": True,
            },
        )
        assert sms.status_code == 201, sms.text
        assert sms.json()["channel"] == "sms"
        email = client.post(
            "/api/v1/contact-attempts",
            headers=headers(),
            json={
                "case_id": "C004",
                "channel": "email",
                "contact_reference": "CONTACT-REF-C004-EMAIL",
                "scheduled_at": "2026-09-20T08:30:00Z",
                "acknowledged": True,
            },
        )
        assert email.status_code == 409
        assert "daily_frequency_exceeded" in email.text
