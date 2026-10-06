from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_case_validation import ledger_case
from test_customer_materials import headers, payload
from test_material_associations import decide, propose

from app import evidence_workspace as workspace
from app.main import create_app
from app.models import CustomerMaterial, ServiceConfig, UsageEvent

__all__ = ["ledger_case"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SECRET_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with TestClient(create_app("sqlite:///:memory:")) as c:
        yield c


def material(c, data=b"case,pay,refund\nC002,0,0\n"):
    r = c.post("/api/v1/customer-materials", headers=headers(), json=payload(data))
    assert r.status_code == 201, r.text
    return r.json()


def current(c):
    r = c.get("/api/v1/evidence-workspace/C002", headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


def test_versions_preserve_originals_invalidate_review_and_scope(client):
    c = client
    m = material(c)
    link = propose(c, m["id"]).json()
    assert decide(c, link).status_code == 200
    p = {"expected_latest_id": m["id"], "material": payload(b"case,pay,refund\nC002,100,0\n")}
    r = c.post(f"/api/v1/material-versions/{m['id']}", headers=headers(), json=p)
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["version"] == 2 and new["is_latest"]
    h = c.get(f"/api/v1/material-versions/{m['id']}", headers=headers())
    assert h.headers["cache-control"] == "no-store"
    assert [i["version"] for i in h.json()["items"]] == [1, 2]
    assert c.post(f"/api/v1/material-versions/{m['id']}", headers=headers(), json=p).status_code == 409
    assert c.get(f"/api/v1/material-versions/{m['id']}", headers=headers("test-viewer", "TENANT_B")).status_code == 404
    assert c.get("/api/v1/material-associations", headers=headers()).json()[0]["effective_status"] == "stale"
    assert propose(c, m["id"]).status_code == 409
    assert (
        c.get(f"/api/v1/customer-materials/{m['id']}/content", headers=headers()).content
        == b"case,pay,refund\nC002,0,0\n"
    )
    with c.app.state.Session() as db:
        assert len(db.scalars(select(CustomerMaterial)).all()) == 2


def test_workspace_citations_tasks_stale_and_no_approval(client):
    c = client
    w = current(c)
    assert w["tasks"] == [] and w["materials"] == []
    body = {"expected_digest": w["evidence_digest"]}
    a = c.post("/api/v1/evidence-workspace/C002/assist", headers=headers(), json=body)
    assert a.status_code == 200, a.text
    advice = a.json()
    assert advice["mode"] == "rules" and advice["advisory_only"]
    source_ids = [i["source_id"] for i in advice["items"]]
    r = c.post(
        "/api/v1/evidence-workspace/C002/tasks",
        headers=headers(),
        json=body | {"source_ids": source_ids, "acknowledged": True},
    )
    assert r.status_code == 201, r.text
    tasks = r.json()["tasks"]
    assert tasks and not r.json()["real_business_verified"]
    assert (
        c.post(
            "/api/v1/evidence-workspace/C002/tasks",
            headers=headers(),
            json=body | {"source_ids": source_ids, "acknowledged": True},
        ).json()["tasks"]
        == tasks
    )
    t = tasks[0]
    url = f"/api/v1/evidence-workspace/C002/tasks/{t['id']}/resolve"
    resolved = c.post(
        url, headers=headers(), json={"expected_version": 1, "reference": "REVIEW/TASK-001", "acknowledged": True}
    )
    assert resolved.status_code == 200, resolved.text
    assert next(v for v in resolved.json()["tasks"] if v["id"] == t["id"])["status"] == "recorded"
    m = material(c)
    propose(c, m["id"])
    assert all(t["effective_status"] == "stale" for t in current(c)["tasks"])
    assert c.post("/api/v1/evidence-workspace/C002/assist", headers=headers(), json=body).status_code == 409
    assert c.get("/api/v1/evidence-workspace/C002", headers=headers("test-viewer", "TENANT_B")).status_code == 404
    assert (
        c.post("/api/v1/evidence-workspace/C002/assist", headers=headers("test-viewer"), json=body).status_code == 403
    )


def test_model_advice_requires_acknowledgement_and_valid_citations(client, monkeypatch):
    c = client
    w = current(c)
    body = {"expected_digest": w["evidence_digest"], "mode": "model"}
    assert c.post("/api/v1/evidence-workspace/C002/assist", headers=headers(), json=body).status_code == 422
    with c.app.state.Session() as db:
        cfg = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "model")
        )
        cfg.connected = True
        db.commit()
    monkeypatch.setattr(workspace, "resolve_secret", lambda *a: "synthetic-model-token")
    captured = []

    def invoke(*args):
        captured.append(args[-1])
        return SimpleNamespace(
            content={
                "items": [
                    {"source_id": "check:source", "explanation": "核对来源缺口", "next_step": "请业务方核对授权文件"}
                ]
            },
            input_tokens=10,
            output_tokens=20,
            estimated_cost_usd=0.001,
            request_digest="a" * 64,
        )

    monkeypatch.setattr(workspace, "invoke_json_model", invoke)
    result = c.post(
        "/api/v1/evidence-workspace/C002/assist", headers=headers(), json=body | {"share_checks_acknowledged": True}
    )
    assert result.status_code == 200, result.text
    assert result.json()["mode"] == "model" and "C002" not in captured[0]
    with c.app.state.Session() as db:
        assert db.scalar(select(UsageEvent).where(UsageEvent.source_type == "evidence_assist")).quantity == 30
    monkeypatch.setattr(
        workspace,
        "invoke_json_model",
        lambda *a: SimpleNamespace(
            content={"items": [{"source_id": "check:invented", "explanation": "虚构", "next_step": "虚构"}]}
        ),
    )
    assert (
        c.post(
            "/api/v1/evidence-workspace/C002/assist", headers=headers(), json=body | {"share_checks_acknowledged": True}
        ).status_code
        == 503
    )


def test_material_replacement_invalidates_accepted_case(ledger_case):
    from test_case_acceptance import create, prepared, review

    c, actor, secret = ledger_case
    link = prepared(c, secret, production=True)
    row, _ = create(c, link)
    assert review(c, row).status_code == 200
    result = c.post(
        f"/api/v1/material-versions/{link['material_id']}",
        headers=headers(),
        json={"expected_latest_id": link["material_id"], "material": payload(b"case,pay,refund\nC901,11000,3000\n")},
    )
    assert result.status_code == 201, result.text
    report = c.get(f"/api/v1/case-acceptances/{row['id']}/report", headers=headers()).json()
    assert report["effective_status"] == "stale" and not report["externally_attested"]
