from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient

from app.domain import utcnow
from app.job_queue import claim_job, heartbeat_job, recover_stale_jobs
from app.jobs import JOB_HANDLERS, execute_claimed_job
from app.main import create_app
from app.models import AgentRun, AsyncJob
from app.worker import DatabaseWorker


def headers(tenant: str = "TENANT_A", actor: str = "test-user") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def enqueue_voice_test(client: TestClient, key: str, tenant: str = "TENANT_A") -> dict:
    response = client.post(
        "/api/v1/integrations/voice/connection-test/jobs",
        headers=headers(tenant),
        json={"idempotency_key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_external_mode_leaves_work_for_independent_worker(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        queued = enqueue_voice_test(client, "worker-external-voice")
        assert queued["status"] == "queued"
        assert queued["attempt"] == 0
        assert queued["lease_owner"] is None
        before = client.get("/api/v1/jobs/queue/health", headers=headers()).json()
        assert before == {
            "mode": "external",
            "status": "degraded",
            "active_workers": 0,
            "queued_jobs": 1,
            "running_jobs": 0,
            "stale_jobs": 0,
            "latest_heartbeat_at": None,
            "lease_seconds": 60,
            "broker_backend": "database",
            "broker_status": "polling",
        }

        worker = DatabaseWorker(app.state.Session, worker_id="worker-test-1")
        assert worker.run_once() is True
        completed = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers()).json()
        assert completed["status"] == "succeeded"
        assert completed["attempt"] == 1
        assert completed["lease_owner"] is None
        after = client.get("/api/v1/jobs/queue/health", headers=headers()).json()
        assert after["status"] == "healthy"
        assert after["active_workers"] == 1
        assert after["queued_jobs"] == 0


def test_only_one_worker_can_claim_a_job_and_renew_its_lease(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        queued = enqueue_voice_test(client, "worker-exclusive-claim")
        claimed = claim_job(app.state.Session, "worker-a")
        assert claimed == queued["id"]
        assert claim_job(app.state.Session, "worker-b") is None
        assert heartbeat_job(app.state.Session, queued["id"], "worker-b") is False
        assert heartbeat_job(app.state.Session, queued["id"], "worker-a") is True


def test_concurrent_workers_never_receive_the_same_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app(f"sqlite:///{tmp_path / 'worker-race.db'}")
    with TestClient(app) as client:
        queued = enqueue_voice_test(client, "worker-concurrent-claim")
        worker_ids = tuple(f"worker-race-{index}" for index in range(6))
        with ThreadPoolExecutor(max_workers=len(worker_ids)) as pool:
            claims = list(pool.map(lambda worker_id: claim_job(app.state.Session, worker_id), worker_ids))
        assert claims.count(queued["id"]) == 1
        assert claims.count(None) == len(worker_ids) - 1


def test_stale_lease_requeues_job_and_closes_orphan_agent_run(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    viewer_headers = headers(actor="test-viewer")
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/agents/sessions",
            headers=viewer_headers,
            json={"scope_type": "global", "title": "Worker recovery"},
        ).json()
        queued = client.post(
            f"/api/v1/agents/sessions/{session['id']}/messages",
            headers=viewer_headers,
            json={"content": "查询 C002 当前状态", "idempotency_key": "worker-recovery-turn"},
        ).json()
        assert claim_job(app.state.Session, "worker-crashed") == queued["id"]
        with app.state.Session() as db:
            job = db.get(AsyncJob, queued["id"])
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.add(
                AgentRun(
                    tenant_id="TENANT_A",
                    session_id=session["id"],
                    job_id=job.id,
                    provider="Hermes Agent",
                    profile="fulfill-agent-v3",
                    status="running",
                    tool_trace=[],
                    evidence=[],
                )
            )
            db.commit()

        assert heartbeat_job(app.state.Session, queued["id"], "worker-crashed") is False
        assert recover_stale_jobs(app.state.Session, "worker-recovery") == 1
        with app.state.Session() as db:
            job = db.get(AsyncJob, queued["id"])
            orphan = db.query(AgentRun).filter(AgentRun.job_id == job.id).one()
            assert job.status == "queued"
            assert job.recovery_count == 1
            assert job.lease_owner is None
            assert orphan.status == "failed"

        worker = DatabaseWorker(app.state.Session, worker_id="worker-replacement")
        assert worker.run_once() is True
        recovered = client.get(f"/api/v1/jobs/{queued['id']}", headers=viewer_headers).json()
        assert recovered["status"] == "succeeded"
        assert recovered["attempt"] == 2
        assert recovered["recovery_count"] == 1
        messages = client.get(f"/api/v1/agents/sessions/{session['id']}/messages", headers=viewer_headers).json()
        assert [message["role"] for message in messages] == ["user", "assistant"]


def test_stale_lease_fails_after_attempt_budget_is_exhausted(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        queued = enqueue_voice_test(client, "worker-attempt-budget")
        assert claim_job(app.state.Session, "worker-crashed") == queued["id"]
        with app.state.Session() as db:
            job = db.get(AsyncJob, queued["id"])
            job.attempt = job.max_attempts
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert recover_stale_jobs(app.state.Session, "worker-recovery") == 1
        exhausted = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers()).json()
        assert exhausted["status"] == "failed"
        assert "最大尝试次数" in exhausted["error"]
        assert exhausted["recovery_count"] == 1


def test_worker_that_lost_its_lease_cannot_overwrite_the_new_owner(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        job = AsyncJob(
            tenant_id="TENANT_A",
            kind="test.lease-fence",
            status="queued",
            payload={},
            created_by="test-user",
        )
        db.add(job)
        db.commit()
        job_id = job.id
    assert claim_job(app.state.Session, "worker-old") == job_id

    def transfer_lease(db, job):
        job.lease_owner = "worker-new"
        job.lease_expires_at = utcnow() + timedelta(seconds=60)
        db.commit()
        return {"must_not_persist": True}

    monkeypatch.setitem(JOB_HANDLERS, "test.lease-fence", transfer_lease)
    assert execute_claimed_job(app.state.Session, job_id, "worker-old") == "lease_lost"
    with app.state.Session() as db:
        fenced = db.get(AsyncJob, job_id)
        assert fenced.status == "running"
        assert fenced.lease_owner == "worker-new"
        assert fenced.result is None


def test_running_job_cancellation_is_cooperative(monkeypatch) -> None:
    monkeypatch.setenv("JOB_EXECUTION_MODE", "external")
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        queued = enqueue_voice_test(client, "worker-cooperative-cancel")
        assert claim_job(app.state.Session, "worker-cancel") == queued["id"]
        requested = client.post(f"/api/v1/jobs/{queued['id']}/cancel", headers=headers()).json()
        assert requested["status"] == "running"
        assert requested["cancel_requested_at"] is not None
        assert execute_claimed_job(app.state.Session, queued["id"], "worker-cancel") == "cancelled"
        cancelled = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers()).json()
        assert cancelled["status"] == "cancelled"
        assert cancelled["result"] is None
        assert cancelled["lease_owner"] is None
