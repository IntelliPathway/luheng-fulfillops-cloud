from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Activity, AuditEvent, ModelReplayRun


def _headers(tenant: str = "TENANT_A", actor: str = "test-user") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_model_replay_job_is_auditable_idempotent_and_side_effect_free() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        queued = client.post(
            "/api/v1/agents/replays/jobs",
            headers=_headers(),
            json={"suite_name": "fulfillops-safe-core", "idempotency_key": "safe-core-release-1"},
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]
        job = client.get(f"/api/v1/jobs/{job_id}", headers=_headers()).json()
        assert job["status"] == "succeeded"
        assert job["result"]["status"] == "passed"
        assert job["result"]["passed_count"] == 4
        assert job["result"]["failed_count"] == 0
        assert len(job["result"]["dataset_digest"]) == 64

        replay_id = job["result"]["replay_run_id"]
        replay = client.get(f"/api/v1/agents/replays/{replay_id}", headers=_headers())
        assert replay.status_code == 200, replay.text
        payload = replay.json()
        assert payload["mode"] == "deterministic-contract"
        assert payload["provider"] == "FulfillOps Sandbox"
        assert [row["case_id"] for row in payload["results"]] == [
            "money-metrics",
            "protected-case",
            "pause-proposal",
            "forbidden-shell",
        ]
        assert all(row["status"] == "passed" for row in payload["results"])
        assert payload["results"][-1]["tool_trace"][0]["status"] == "blocked"
        assert client.get(f"/api/v1/agents/replays/{replay_id}", headers=_headers("TENANT_B")).status_code == 404

        repeated = client.post(
            "/api/v1/agents/replays/jobs",
            headers=_headers(),
            json={"suite_name": "fulfillops-safe-core", "idempotency_key": "safe-core-release-1"},
        )
        assert repeated.json()["id"] == job_id
        assert len(client.get("/api/v1/agents/replays", headers=_headers()).json()) == 1

        with app.state.Session() as db:
            activity = db.scalar(
                select(Activity).where(Activity.tenant_id == "TENANT_A", Activity.activity_id == "ACT-001")
            )
            assert activity.status == "running"
            stored = db.get(ModelReplayRun, replay_id)
            assert stored.status == "passed"
            audit = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == replay_id,
                    AuditEvent.action == "agent.replay.completed",
                )
            )
            assert audit.detail["dataset_digest"] == payload["dataset_digest"]
            assert "本月回款" not in str(audit.detail)


def test_model_replay_requires_admin_role() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agents/replays/jobs",
            headers=_headers(actor="test-operator"),
            json={"suite_name": "fulfillops-safe-core"},
        )
        assert response.status_code == 403
