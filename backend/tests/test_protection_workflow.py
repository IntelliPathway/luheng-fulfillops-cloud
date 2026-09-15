from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Activity, AuditEvent, CaseRecord, ProtectionIncident


@pytest.fixture()
def client() -> TestClient:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def headers(tenant: str = "TENANT_A", actor: str = "test-user") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def open_incident(
    client: TestClient,
    case_id: str,
    source_event_id: str,
    category: str = "debt_dispute",
    *,
    actor: str = "test-operator",
    tenant: str = "TENANT_A",
    reason: str = "收到新的业务异议，必须立即停止主动触达",
):
    return client.post(
        f"/api/v1/cases/{case_id}/protections",
        headers=headers(tenant, actor),
        json={
            "source_event_id": source_event_id,
            "category": category,
            "reason": reason,
            "owner": "测试责任人",
            "sla_hours": 4,
            "acknowledged": True,
        },
    )


def propose_resolution(
    client: TestClient,
    incident_id: str,
    *,
    actor: str = "test-operator",
    evidence_refs: list[str] | None = None,
):
    return client.post(
        f"/api/v1/protections/incidents/{incident_id}/resolution-proposals",
        headers=headers(actor=actor),
        json={
            "resolution_note": "相关事实已经复核，证据已归档，申请重新评估案件",
            "evidence_refs": evidence_refs or ["EVIDENCE-TEST-001"],
            "acknowledged": True,
        },
    )


def decide_resolution(
    client: TestClient,
    incident_id: str,
    version: int,
    *,
    actor: str = "Terry",
    decision: str = "approve",
):
    return client.post(
        f"/api/v1/protections/incidents/{incident_id}/decision",
        headers=headers(actor=actor),
        json={
            "decision": decision,
            "review_note": "已独立核对案件事实和证据引用，复核结论一致",
            "expected_version": version,
            "acknowledged": True,
        },
    )


def test_open_is_idempotent_tenant_scoped_and_immediately_blocks_execution(client: TestClient) -> None:
    response = open_incident(client, "C002", "GUARD-OPEN-001")
    assert response.status_code == 201, response.text
    incident = response.json()
    assert incident["status"] == "open"
    assert incident["priority"] == "P0"
    assert len(incident["opening_digest"]) == 64

    repeated = open_incident(client, "C002", "GUARD-OPEN-001")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == incident["id"]
    conflict = open_incident(
        client,
        "C002",
        "GUARD-OPEN-001",
        reason="同一来源事件不允许被改写为另一条保护事实",
    )
    assert conflict.status_code == 409
    assert "protection_event_conflict" in conflict.json()["detail"]
    assert open_incident(client, "C002", "GUARD-CROSS-TENANT", tenant="TENANT_B").status_code == 404

    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C002"))
        activity = db.scalar(select(Activity).where(Activity.tenant_id == "TENANT_A", Activity.activity_id == "ACT-001"))
        assert case is not None and case.blocked is True and case.status == "异议暂停"
        assert activity is not None and activity.status == "blocked"
        assert activity.preflight["protection_event_id"] == "GUARD-OPEN-001"


def test_viewer_cannot_open_or_change_protection(client: TestClient) -> None:
    denied = open_incident(client, "C008", "GUARD-VIEWER-001", actor="test-viewer")
    assert denied.status_code == 403
    overview = client.get("/api/v1/protections/overview", headers=headers(actor="test-viewer"))
    assert overview.status_code == 200
    assert all(row["case_id"] != "C021" for row in overview.json()["incidents"])
    assert client.get(
        "/api/v1/protections/incidents",
        headers=headers("TENANT_B", "test-viewer"),
    ).json() == []


def test_resolution_requires_evidence_version_and_independent_admin(client: TestClient) -> None:
    opened = open_incident(client, "C008", "GUARD-RESOLVE-001").json()
    missing_ack = client.post(
        f"/api/v1/protections/incidents/{opened['id']}/resolution-proposals",
        headers=headers(actor="test-user"),
        json={
            "resolution_note": "相关事实已经复核，申请重新评估案件",
            "evidence_refs": ["EVIDENCE-TEST-002"],
            "acknowledged": False,
        },
    )
    assert missing_ack.status_code == 422

    proposal = propose_resolution(client, opened["id"], actor="test-user")
    assert proposal.status_code == 200, proposal.text
    review = proposal.json()
    assert review["status"] == "pending_review"
    assert review["version"] == 2
    assert len(review["evidence_digest"]) == 64
    repeated = propose_resolution(client, opened["id"], actor="test-user")
    assert repeated.status_code == 200
    assert repeated.json()["version"] == 2

    self_review = decide_resolution(client, opened["id"], 2, actor="test-user")
    assert self_review.status_code == 409
    assert "maker_checker_conflict" in self_review.json()["detail"]
    stale = decide_resolution(client, opened["id"], 1)
    assert stale.status_code == 409
    assert "resolution_version_conflict" in stale.json()["detail"]

    approved = decide_resolution(client, opened["id"], 2)
    assert approved.status_code == 200, approved.text
    decided = approved.json()
    assert decided["status"] == "resolved"
    assert decided["case_released"] is True
    assert decided["version"] == 3
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C008"))
        assert case is not None and case.blocked is False and case.status == "待重新评估"
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == "TENANT_A",
                    AuditEvent.resource_id == opened["id"],
                )
            )
        )
        assert [event.action for event in events] == [
            "protection.incident.opened",
            "protection.resolution.proposed",
            "protection.resolution.approve",
        ]
        assert all("相关事实已经复核" not in str(event.detail) for event in events)


def test_other_active_incident_keeps_case_protected_after_approval(client: TestClient) -> None:
    first = open_incident(client, "C002", "GUARD-MULTI-001").json()
    second = open_incident(
        client,
        "C002",
        "GUARD-MULTI-002",
        category="identity_conflict",
        reason="身份资料存在冲突，核验一致前禁止披露案件信息",
    ).json()
    proposal = propose_resolution(client, first["id"]).json()
    approved = decide_resolution(client, first["id"], proposal["version"])
    assert approved.status_code == 200
    assert approved.json()["case_released"] is False
    assert second["status"] == "open"
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C002"))
        assert case is not None and case.blocked is True


def test_permanent_hold_and_mandate_renewal_fail_closed(client: TestClient) -> None:
    permanent = open_incident(client, "C008", "GUARD-STOP-001", category="stop_contact").json()
    assert permanent["status"] == "permanent_hold"
    blocked = propose_resolution(client, permanent["id"])
    assert blocked.status_code == 409
    assert "permanent_protection" in blocked.json()["detail"]

    mandate = open_incident(client, "C002", "GUARD-MANDATE-001", category="mandate_expired").json()
    missing_renewal = propose_resolution(client, mandate["id"], evidence_refs=["EVIDENCE-CONTRACT-001"])
    assert missing_renewal.status_code == 422
    assert "mandate_renewal_evidence_required" in missing_renewal.json()["detail"]
    accepted = propose_resolution(client, mandate["id"], evidence_refs=["MANDATE-RENEWAL-001"])
    assert accepted.status_code == 200


def test_rejected_resolution_keeps_protection_and_can_be_resubmitted(client: TestClient) -> None:
    opened = open_incident(client, "C008", "GUARD-REJECT-001").json()
    proposal = propose_resolution(client, opened["id"]).json()
    rejected = decide_resolution(client, opened["id"], proposal["version"], decision="reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "open"
    assert rejected.json()["case_released"] is False
    second = propose_resolution(client, opened["id"], evidence_refs=["EVIDENCE-TEST-REVISED"])
    assert second.status_code == 200
    assert second.json()["version"] == 4
    with client.app.state.Session() as db:
        incident = db.get(ProtectionIncident, opened["id"])
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C008"))
        assert incident is not None and incident.status == "pending_review"
        assert case is not None and case.blocked is True
