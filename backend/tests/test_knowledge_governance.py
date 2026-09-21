from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "Terry", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_knowledge_version_requires_independent_review_and_retires_previous() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        payload = {
            "document_key": "KNOW-PAY",
            "title": "履约到账口径",
            "category": "policy",
            "source_reference": "policy://payment/v1",
            "content_digest": "a" * 64,
            "summary": "只有经过验签并写入不可变账簿的回执才构成已确认到账。",
            "acknowledged": True,
        }
        created = client.post("/api/v1/knowledge/documents", headers=headers(), json=payload)
        assert created.status_code == 201, created.text
        document = created.json()

        self_review = client.post(
            f"/api/v1/knowledge/documents/{document['id']}/decision",
            headers=headers(),
            json={"decision": "approve", "expected_version": 1, "review_note": "尝试自行复核知识版本", "acknowledged": True},
        )
        assert self_review.status_code == 409

        approved = client.post(
            f"/api/v1/knowledge/documents/{document['id']}/decision",
            headers=headers("test-user"),
            json={"decision": "approve", "expected_version": 1, "review_note": "已核对政策来源和内容摘要", "acknowledged": True},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "published"

        second = client.post(
            "/api/v1/knowledge/documents",
            headers=headers(),
            json={**payload, "content_digest": "b" * 64, "source_reference": "policy://payment/v2"},
        ).json()
        assert second["version"] == 2
        client.post(
            f"/api/v1/knowledge/documents/{second['id']}/decision",
            headers=headers("test-user"),
            json={"decision": "approve", "expected_version": 2, "review_note": "新版来源和摘要均已独立核验", "acknowledged": True},
        )
        rows = client.get("/api/v1/knowledge/documents", headers=headers()).json()
        assert {row["status"] for row in rows} == {"published", "retired"}


def test_knowledge_documents_are_tenant_scoped() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        assert client.get("/api/v1/knowledge/documents", headers=headers(tenant="TENANT_B")).json() == []
