from fastapi.testclient import TestClient

from app.main import create_app


def headers(actor: str, tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_strategy_experiment_requires_maker_checker_and_exposes_results() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        created = client.post(
            "/api/v1/strategy-experiments",
            headers=headers("test-operator"),
            json={
                "package_id": "PKG_A",
                "name": "协商节奏候选策略",
                "hypothesis": "新的协商节奏可以提升确认回款率",
                "candidate_policy_version": 2,
                "allocation_bps": 2500,
                "success_metric": "confirmed_recovery_rate",
                "acknowledged": True,
            },
        )
        assert created.status_code == 201, created.text
        experiment = created.json()
        self_start = client.post(
            f"/api/v1/strategy-experiments/{experiment['id']}/transition",
            headers=headers("test-operator"),
            json={"action": "start", "expected_version": 1, "acknowledged": True},
        )
        assert self_start.status_code in {403, 409}
        started = client.post(
            f"/api/v1/strategy-experiments/{experiment['id']}/transition",
            headers=headers("test-user"),
            json={"action": "start", "expected_version": 1, "acknowledged": True},
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "running"
        results = client.get(
            f"/api/v1/strategy-experiments/{experiment['id']}/results",
            headers=headers("test-viewer"),
        ).json()
        assert results["assignment"] == "sha256-stable-v1"
        assert set(results["cohorts"]) == {"control", "candidate"}


def test_strategy_experiments_are_tenant_scoped() -> None:
    with TestClient(create_app("sqlite:///:memory:")) as client:
        tenant_a = client.get("/api/v1/strategy-experiments", headers=headers("test-viewer")).json()
        tenant_b = client.get("/api/v1/strategy-experiments", headers=headers("test-viewer", "TENANT_B")).json()
        assert tenant_a == []
        assert tenant_b == []
