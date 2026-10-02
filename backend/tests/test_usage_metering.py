from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "test-user", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_agent_run_creates_one_idempotent_usage_event_and_invoice_line() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        session = client.post(
            "/api/v1/agents/sessions",
            headers=headers("test-viewer"),
            json={"scope_type": "global", "title": "计量测试"},
        ).json()
        payload = {"content": "汇总当前业务状态", "idempotency_key": "usage-turn-1"}
        first = client.post(
            f"/api/v1/agents/sessions/{session['id']}/messages",
            headers=headers("test-viewer"),
            json=payload,
        )
        assert first.status_code == 202, first.text
        repeated = client.post(
            f"/api/v1/agents/sessions/{session['id']}/messages",
            headers=headers("test-viewer"),
            json=payload,
        )
        assert repeated.status_code == 202
        assert repeated.json()["id"] == first.json()["id"]

        period = datetime.now(UTC).strftime("%Y-%m")
        invoice = client.get(f"/api/v1/billing/invoice-preview?period={period}", headers=headers("test-viewer")).json()
        agent_line = next(line for line in invoice["lines"] if line["meter"] == "agent_run")
        assert agent_line == {"meter": "agent_run", "unit": "run", "quantity": 1, "amount_cents": 5, "event_count": 1}
        assert invoice["automatic_charge"] is False
        events = client.get(f"/api/v1/billing/usage-events?period={period}", headers=headers()).json()
        assert len(events) == 1
        assert events[0]["source_type"] == "agent_run"


def test_usage_ledger_is_tenant_scoped() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        period = datetime.now(UTC).strftime("%Y-%m")
        tenant_b = client.get(
            f"/api/v1/billing/invoice-preview?period={period}",
            headers=headers("test-viewer", "TENANT_B"),
        ).json()
        assert tenant_b["lines"] == []
        assert tenant_b["subtotal_cents"] == 0
