from fastapi.testclient import TestClient
from test_case_validation import ledger_case, post_report_payment
from test_customer_materials import headers, payload

from app.main import create_app
from app.models import PaymentReceipt

__all__ = ["ledger_case"]


def propose(client, material_id, case_id="C002"):
    return client.post(
        "/api/v1/material-associations",
        headers=headers(),
        json={"material_id": material_id, "case_id": case_id, "acknowledged": True},
    )


def decide(client, row, actor="test-user", decision="approve"):
    return client.post(
        f"/api/v1/material-associations/{row['id']}/decision",
        headers=headers(actor),
        json={
            "decision": decision,
            "expected_version": row["version"],
            "decision_reference": "REVIEW/TEST-001",
            "acknowledged": True,
        },
    )


def test_association_independent_review_and_scope(monkeypatch):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with TestClient(create_app("sqlite:///:memory:")) as c:
        m = c.post("/api/v1/customer-materials", headers=headers(), json=payload()).json()
        suggest = c.get(f"/api/v1/material-associations/suggestions/{m['id']}", headers=headers()).json()
        assert suggest["suggestions"][0]["can_propose"] and not suggest["automatic_approval"]
        r = propose(c, m["id"])
        assert r.status_code == 201, r.text
        row = r.json()
        assert propose(c, m["id"]).json()["id"] == row["id"]
        assert decide(c, row, "Terry").status_code == 409
        assert decide(c, row, "test-viewer").status_code == 403
        approved = decide(c, row)
        assert approved.status_code == 200, approved.text
        assert approved.json()["effective_status"] == "approved"
        assert not approved.json()["externally_attested"]
        assert decide(c, row).status_code == 409
        assert c.get("/api/v1/material-associations", headers=headers("test-viewer", "TENANT_B")).json() == []
        assert propose(c, m["id"], "C001").status_code == 422
        assert propose(c, m["id"], "MISSING").status_code == 404


def test_association_evidence_changes_require_reproposal(ledger_case):
    c, actor, secret = ledger_case
    receipt = post_report_payment(c, secret, "REPORT-PAYMENT")
    m = c.post("/api/v1/customer-materials", headers=headers(), json=payload(b"case,pay,refund\nC901,10000,0\n")).json()
    row = propose(c, m["id"], "C901").json()
    with c.app.state.Session() as db:
        db.get(PaymentReceipt, receipt["id"]).signature_verified = False
        db.commit()
    assert decide(c, row).status_code == 409
    assert decide(c, row, decision="reject").status_code == 409
    unchanged = c.get("/api/v1/material-associations", headers=headers()).json()[0]
    assert unchanged["version"] == row["version"] and unchanged["decision_reference"] is None
    assert c.get("/api/v1/material-associations", headers=headers()).json()[0]["effective_status"] == "stale"
    fresh = propose(c, m["id"], "C901").json()
    assert fresh["version"] > row["version"]
    rejected = decide(c, fresh, decision="reject")
    assert rejected.status_code == 200
    assert propose(c, m["id"], "C901").json()["status"] == "pending_review"
