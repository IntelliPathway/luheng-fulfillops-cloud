from dataclasses import replace
from datetime import timedelta

from test_case_validation import ledger_case, post_report_payment
from test_customer_materials import headers, payload
from test_material_associations import decide, propose

from app.models import CaseAcceptance, PaymentReceipt, utcnow

__all__ = ["ledger_case"]


def prepared(client, secret, *, production=False, mismatched=False):
    post_report_payment(client, secret, "REPORT-PAYMENT")
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    if production:
        client.app.state.startup = replace(client.app.state.startup, environment="production", seed_demo_data=False)
    m = client.post(
        "/api/v1/customer-materials",
        headers=headers(),
        json=payload(b"case,pay,refund\nC901," + (b"12000,5000" if mismatched else b"10000,3000") + b"\n"),
    ).json()
    link = propose(client, m["id"], "C901").json()
    reviewed = decide(client, link)
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def create(client, link, request_id="REQUEST-001"):
    p = {
        "association_id": link["id"],
        "idempotency_key": request_id,
        "external_reference": "CUSTOMER/TEST-001",
        "external_digest": "a" * 64,
        "acknowledged": True,
    }
    r = client.post("/api/v1/case-acceptances", headers=headers(), json=p)
    assert r.status_code == 201, r.text
    return r.json(), p


def review(client, row, actor="test-user", decision="accept"):
    return client.post(
        f"/api/v1/case-acceptances/{row['id']}/decision",
        headers=headers(actor),
        json={
            "decision": decision,
            "expected_version": row["version"],
            "decision_reference": "DECISION/TEST-001",
            "acknowledged": True,
        },
    )


def test_acceptance_independent_review_reports_and_scope(ledger_case):
    c, actor, secret = ledger_case
    link = prepared(c, secret, production=True)
    row, p = create(c, link)
    assert c.post("/api/v1/case-acceptances", headers=headers(), json=p).json()["id"] == row["id"]
    assert (
        c.post("/api/v1/case-acceptances", headers=headers(), json=p | {"external_digest": "b" * 64}).status_code == 409
    )
    assert review(c, row, "Terry").status_code == 409
    assert review(c, row, "test-viewer").status_code == 403
    result = review(c, row)
    assert result.status_code == 200, result.text
    assert result.json()["externally_attested"]
    assert not result.json()["real_business_verified"] and not result.json()["enables_external_execution"]
    assert result.json()["attestation_method"] == "independent_admin_record_review"
    assert review(c, row).status_code == 409
    r = c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers())
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["effective_status"] == "accepted" and len(r.json()["report_digest"]) == 64
    assert (
        c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers("test-viewer", "TENANT_B")).status_code
        == 404
    )
    assert c.get("/api/v1/case-acceptances", headers=headers("test-viewer", "TENANT_B")).json() == []
    with c.app.state.Session() as db:
        db.get(CaseAcceptance, row["id"]).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert (
        c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers()).json()["effective_status"] == "expired"
    )


def test_development_gate_and_offsetting_amounts_cannot_pass(ledger_case):
    c, actor, secret = ledger_case
    link = prepared(c, secret, mismatched=True)
    row, _ = create(c, link)
    assert review(c, row).status_code == 409
    checks = {v["id"]: v["passed"] for v in row["current_evidence"]["checks"]}
    assert not checks["amounts"] and not checks["environment"]
    assert review(c, row, decision="reject").json()["effective_status"] == "rejected"


def test_acceptance_invalidates_on_evidence_and_configuration_change(ledger_case, monkeypatch):
    c, actor, secret = ledger_case
    link = prepared(c, secret, production=True)
    row, _ = create(c, link)
    monkeypatch.setenv("PILOT_DEPLOYMENT_REVISION", "changed-revision")
    assert review(c, row).status_code == 409
    assert (
        c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers()).json()["effective_status"] == "stale"
    )
    row2, _ = create(c, link, "REQUEST-002")
    with c.app.state.Session() as db:
        from sqlalchemy import select

        receipt = db.scalar(select(PaymentReceipt).where(PaymentReceipt.tenant_id == "TENANT_A"))
        receipt.signature_verified = False
        db.commit()
    assert review(c, row2).status_code == 409


def test_external_statement_tampering_invalidates_acceptance(ledger_case):
    c, actor, secret = ledger_case
    link = prepared(c, secret, production=True)
    row, _ = create(c, link)
    with c.app.state.Session() as db:
        db.get(CaseAcceptance, row["id"]).external_digest = "b" * 64
        db.commit()
    assert review(c, row).status_code == 409
    assert (
        c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers()).json()["effective_status"] == "stale"
    )
