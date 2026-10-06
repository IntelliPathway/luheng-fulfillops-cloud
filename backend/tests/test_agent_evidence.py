from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AgentRun, AgentSession


@pytest.fixture()
def client():
    with TestClient(create_app("sqlite:///:memory:")) as value:
        yield value


def headers(actor="test-user", tenant="TENANT_A"):
    return {"X-Actor-ID": actor, "X-Tenant-ID": tenant}


def seed_run(db, tenant, owner, scope, activity, name):
    session = AgentSession(
        tenant_id=tenant,
        created_by=owner,
        scope_type=scope,
        scope_id=activity,
        runtime_provider="contract",
        runtime_profile="safe",
    )
    db.add(session)
    db.flush()
    db.add(
        AgentRun(
            id=name,
            tenant_id=tenant,
            session_id=session.id,
            provider="contract",
            profile="safe",
            status="succeeded",
            tool_trace=[{"tool": "activity.read", "status": "completed", "detail": "persisted evidence"}],
            evidence=[],
        )
    )


def test_activity_evidence_is_persisted_scope_and_owner_filtered(client):
    activity = client.get("/api/v1/activities", headers=headers()).json()[0]["activity_id"]
    with client.app.state.Session() as db:
        seed_run(db, "TENANT_A", "test-operator", "activity", activity, "RUN-OWN")
        seed_run(db, "TENANT_A", "Terry", "activity", activity, "RUN-OTHER")
        seed_run(db, "TENANT_A", "test-operator", "global", activity, "RUN-GLOBAL")
        seed_run(db, "TENANT_B", "test-operator", "activity", activity, "RUN-OTHER-TENANT")
        db.commit()
    path = f"/api/v1/agents/activities/{activity}/runs"
    rows = client.get(path, headers=headers("test-operator")).json()
    assert [r["id"] for r in rows] == ["RUN-OWN"]
    assert rows[0]["tool_trace"][0]["detail"] == "persisted evidence"
    admin_rows = client.get(path, headers=headers()).json()
    assert {r["id"] for r in admin_rows} == {"RUN-OWN", "RUN-OTHER"}
    assert len(client.get(path + "?limit=1", headers=headers()).json()) == 1
    assert client.get(path + "?limit=51", headers=headers()).status_code == 422
    assert client.get(path).status_code == 401


def test_unknown_or_other_tenant_activity_never_has_runs(client):
    assert client.get("/api/v1/agents/activities/UNKNOWN/runs", headers=headers()).status_code == 404
    activity = client.get("/api/v1/activities", headers=headers()).json()[0]["activity_id"]
    assert (
        client.get(f"/api/v1/agents/activities/{activity}/runs", headers=headers(tenant="TENANT_B")).status_code == 404
    )


def test_activity_summary_is_complete_fresh_and_includes_refunds(client):
    from app.models import RecoveryLedgerEntry, utcnow

    activity = client.get("/api/v1/activities", headers=headers()).json()[0]
    case_id = activity["case_ids"][0]
    path = f"/api/v1/agents/activities/{activity['activity_id']}/summary"
    before = client.get(path, headers=headers()).json()
    with client.app.state.Session() as db:
        for index in range(501):
            db.add(
                RecoveryLedgerEntry(
                    tenant_id="TENANT_A",
                    entry_id=f"COMPLETE-{index}",
                    case_id=case_id,
                    package_id=activity["package_id"],
                    event_type="payment",
                    amount_cents=100,
                    eligible_amount_cents=100,
                    commission_rule_id="COM-TEST",
                    commission_rule_version=1,
                    rate_bps=1500,
                    commission_cents=15,
                    reason="eligible",
                    source="synthetic regression",
                    booked_at=utcnow(),
                )
            )
        db.add(
            RecoveryLedgerEntry(
                tenant_id="TENANT_A",
                entry_id="COMPLETE-REFUND",
                case_id=case_id,
                package_id=activity["package_id"],
                event_type="refund",
                amount_cents=-300,
                eligible_amount_cents=-300,
                commission_rule_id="COM-TEST",
                commission_rule_version=1,
                rate_bps=1500,
                commission_cents=-45,
                reason="refund",
                source="synthetic regression",
                booked_at=utcnow(),
            )
        )
        db.commit()
    after = client.get(path, headers=headers()).json()
    assert after["confirmed_net_recovery_cents"] - before["confirmed_net_recovery_cents"] == 49800
    assert after["accrued_commission_cents"] - before["accrued_commission_cents"] == 7470
    assert after["ledger_entry_count"] - before["ledger_entry_count"] == 502
    assert after["activity_attribution"] is False
    assert client.get(path, headers=headers(tenant="TENANT_B")).status_code == 404
