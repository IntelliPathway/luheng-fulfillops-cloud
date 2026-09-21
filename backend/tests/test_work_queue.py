from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str = "Terry", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_work_queue_unifies_cross_domain_risk_in_priority_order() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        response = client.get("/api/v1/operations/work-queue", headers=headers())
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["tenant_id"] == "TENANT_A"
        assert payload["total"] == sum(payload["summary"].values())
        priorities = [item["priority"] for item in payload["items"]]
        assert priorities == sorted(priorities, key={"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get)
        assert all(item["risk"] and item["evidence_ref"] and item["required_role"] for item in payload["items"])
        assert all(
            item["route"] in {"control", "strategy", "payments", "plans", "exceptions"} for item in payload["items"]
        )


def test_work_queue_is_tenant_scoped_and_readable_by_viewers() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        tenant_a = client.get("/api/v1/operations/work-queue?limit=2", headers=headers("test-viewer")).json()
        tenant_b = client.get("/api/v1/operations/work-queue", headers=headers(tenant="TENANT_B")).json()
        assert len(tenant_a["items"]) <= 2
        assert all(item["id"] not in {other["id"] for other in tenant_b["items"]} for item in tenant_a["items"])
