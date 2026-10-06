import base64
import hashlib

import pytest
from fastapi.testclient import TestClient
from test_case_validation import ledger_case, post_report_payment

from app.main import create_app
from app.models import CustomerMaterial, PaymentReceipt

# Shared signed ledger fixture; all material content below is synthetic.
__all__ = ["ledger_case"]


def headers(actor="Terry", tenant="TENANT_A"):
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def payload(raw=b"case,pay,refund\nC002,10000,3000\n"):
    return {
        "filename": "claims.csv",
        "source_reference": "BANK/TEST-001",
        "file_kind": "csv",
        "content_base64": base64.b64encode(raw).decode(),
        "mapping": {"case_id": "case", "payment_cents": "pay", "refund_cents": "refund"},
        "acknowledged": True,
    }


def test_encrypted_roundtrip_scope_idempotency_and_integrity(monkeypatch):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        p = payload(b'case,pay,refund,private_note\nC002,10000,3000,"synthetic-private-content"\n')
        r = client.post("/api/v1/customer-materials", headers=headers(), json=p)
        assert r.status_code == 201, r.text
        m = r.json()
        assert m["source_digest"] == hashlib.sha256(base64.b64decode(p["content_base64"])).hexdigest()
        assert "ciphertext" not in m and "normalized_rows" not in m
        assert client.post("/api/v1/customer-materials", headers=headers(), json=p).json()["id"] == m["id"]
        assert client.get("/api/v1/customer-materials", headers=headers("test-viewer", "TENANT_B")).json() == []
        for suffix in ("content", "report"):
            assert client.get(
                f"/api/v1/customer-materials/{m['id']}/{suffix}", headers=headers("test-viewer", "TENANT_B")
            ).status_code in {403, 404}
        assert (
            client.get(f"/api/v1/customer-materials/{m['id']}/content", headers=headers("test-viewer")).status_code
            == 403
        )
        content = client.get(f"/api/v1/customer-materials/{m['id']}/content", headers=headers())
        assert content.content == base64.b64decode(p["content_base64"])
        assert content.headers["cache-control"] == "no-store"
        report = client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=headers()).json()
        assert report["results"][0]["status"] == "unavailable"
        assert report["results"][0]["payment_difference_cents"] is None
        with app.state.Session() as db:
            row = db.get(CustomerMaterial, m["id"])
            assert "synthetic-private-content" not in row.ciphertext
            assert "private_note" not in str(row.normalized_rows)
            row.ciphertext = "AAAA"
            db.commit()
        assert client.get(f"/api/v1/customer-materials/{m['id']}/content", headers=headers()).status_code == 503
        assert client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=headers()).status_code == 503


def test_material_comparison_checks_both_amounts_and_never_attests(ledger_case):
    client, actor, secret = ledger_case
    receipt = post_report_payment(client, secret, "REPORT-PAYMENT")
    post_report_payment(client, secret, "REPORT-REFUND", refund=True)
    for expected, status, differences in [
        (b"10000,3000", "matched", (0, 0)),
        (b"12000,5000", "mismatch", (-2000, -2000)),
    ]:
        m = client.post(
            "/api/v1/customer-materials", headers=actor, json=payload(b"case,pay,refund\nC901," + expected + b"\n")
        ).json()
        report = client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=actor).json()
        r = report["results"][0]
        assert r["status"] == status
        assert (r["payment_difference_cents"], r["refund_difference_cents"]) == differences
        assert (
            not report["real_business_verified"]
            and not report["externally_attested"]
            and not report["enables_external_execution"]
        )
    with client.app.state.Session() as db:
        db.get(PaymentReceipt, receipt["id"]).signature_verified = False
        db.commit()
    report = client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=actor).json()
    assert report["results"][0]["status"] == "unavailable"
    assert report["results"][0]["refund_difference_cents"] is None
    m = client.post("/api/v1/customer-materials", headers=actor, json=payload(b"case,pay,refund\nMISSING,0,0\n")).json()
    assert (
        client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=actor).json()["results"][0]["status"]
        == "case_not_found"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"case,pay,refund\nC002,1.0,0\n",
        b"case,pay,refund\nC002,01,0\n",
        b"case,pay,refund\nC002,-1,0\n",
        b"case,pay,refund\nC002,9007199254740992,0\n",
        b"case,pay,refund\nC002,1,0\nC002,2,0\n",
        b"case,pay,pay\nC002,1,0\n",
        b"case,pay,refund\n",
        b"\xff",
        b"case,pay,refund\nC002,0,0\n" * 52,
    ],
)
def test_invalid_csv_is_atomic(monkeypatch, raw):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with TestClient(create_app("sqlite:///:memory:")) as client:
        r = client.post("/api/v1/customer-materials", headers=headers(), json=payload(raw))
        assert r.status_code == 422, r.text
        assert client.get("/api/v1/customer-materials", headers=headers()).json() == []


def test_attachments_permission_and_missing_key(monkeypatch):
    monkeypatch.delenv("SECRET_MASTER_KEY", raising=False)
    with TestClient(create_app("sqlite:///:memory:")) as client:
        assert client.post("/api/v1/customer-materials", headers=headers(), json=payload()).status_code == 503
        monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        assert (
            client.post("/api/v1/customer-materials", headers=headers("test-viewer"), json=payload()).status_code == 403
        )
        p = payload(b"%PDF-1.7\nsynthetic attachment") | {"filename": "evidence.pdf", "file_kind": "pdf", "mapping": {}}
        m = client.post("/api/v1/customer-materials", headers=headers(), json=p).json()
        assert (
            client.get(f"/api/v1/customer-materials/{m['id']}/report", headers=headers()).json()["status"]
            == "attachment_only"
        )
        assert (
            client.post("/api/v1/customer-materials", headers=headers(), json=p | {"acknowledged": False}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/customer-materials", headers=headers(), json=p | {"filename": "../evidence.pdf"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/customer-materials", headers=headers(), json=p | {"content_base64": "AAAA"}
            ).status_code
            == 422
        )
