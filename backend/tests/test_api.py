from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AgentRun, CaseFinancialProfile


@pytest.fixture()
def client() -> TestClient:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def headers(tenant: str = "TENANT_A", role: str = "admin") -> dict[str, str]:
    actor = {"admin": "test-user", "operator": "test-operator", "viewer": "test-viewer"}[role]
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor, "X-Role": "admin"}


def connect_remaining_services(client: TestClient) -> None:
    for service in ("voice", "phone"):
        response = client.post(f"/api/v1/integrations/{service}/connection-test", headers=headers())
        assert response.status_code == 200, response.text


def enable_channels(client: TestClient) -> None:
    connect_remaining_services(client)
    report = client.post("/api/v1/integrations/self-test", headers=headers())
    assert report.status_code == 200
    assert report.json()["status"] == "passed"
    enabled = client.post("/api/v1/integrations/enable", headers=headers(), json={"acknowledged": True})
    assert enabled.status_code == 200
    assert enabled.json()["gate"]["ready"] is True


def test_health_and_tenant_boundary(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/integrations").status_code == 401
    assert client.get("/api/v1/integrations", headers=headers("UNKNOWN")).status_code == 404


def test_initial_integration_state_is_tenant_scoped(client: TestClient) -> None:
    tenant_a = client.get("/api/v1/integrations", headers=headers()).json()
    tenant_b = client.get("/api/v1/integrations", headers=headers("TENANT_B")).json()
    assert set(tenant_a["services"]) == {"agent", "model", "voice", "phone"}
    assert tenant_a["services"]["agent"]["connected"] is True
    assert tenant_a["services"]["voice"]["connected"] is False
    assert tenant_a["services"]["model"]["credential_mask"] == "••••DEMO"
    assert tenant_b["services"] == {}


def test_plaintext_credentials_are_rejected_inside_settings(client: TestClient) -> None:
    response = client.put(
        "/api/v1/integrations/model",
        headers=headers(),
        json={
            "provider": "DeepSeek",
            "settings": {
                "endpoint": "https://example.test",
                "model": "chat",
                "timeout": "30",
                "apiKey": "should-not-be-here",
            },
            "credential": "safe-input-channel",
        },
    )
    assert response.status_code == 422
    assert "credential" in response.json()["detail"]


def test_full_enablement_and_config_invalidation(client: TestClient) -> None:
    enable_channels(client)
    current = client.get("/api/v1/integrations", headers=headers()).json()
    model = current["services"]["model"]
    response = client.put(
        "/api/v1/integrations/model",
        headers=headers(),
        json={"provider": model["provider"], "settings": model["settings"], "credential": "replacement-secret-9876"},
    )
    assert response.status_code == 200
    changed = response.json()
    assert changed["services"]["model"]["version"] == model["version"] + 1
    assert changed["services"]["model"]["credential_mask"] == "••••9876"
    assert changed["services"]["model"]["connected"] is False
    assert changed["state"]["enabled"] is False
    assert changed["last_report"] is None
    assert changed["gate"]["ready"] is False


def test_only_admin_can_change_integration_configuration(client: TestClient) -> None:
    response = client.post("/api/v1/integrations/voice/connection-test", headers=headers(role="operator"))
    assert response.status_code == 403


def test_activity_preflight_excludes_completed_and_inflight_cases(client: TestClient) -> None:
    payload = {
        "name": "首次联络",
        "package_id": "PKG_A",
        "goal": "首次联络与意愿确认",
        "budget_yuan": 20,
        "case_ids": ["C001", "C002", "C008"],
        "requested_mode": "auto",
    }
    response = client.post("/api/v1/activities/preflight", headers=headers(role="operator"), json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["eligible_case_ids"] == ["C008"]
    assert {item["case_id"]: item["reason"] for item in result["excluded"]} == {
        "C001": "已完成",
        "C002": "重复在途任务",
    }
    assert result["resolved_mode"] == "sandbox"


def test_activity_preflight_excludes_a_future_mandate(client: TestClient) -> None:
    with client.app.state.Session() as db:
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == "TENANT_A",
                CaseFinancialProfile.case_id == "C008",
            )
        )
        assert profile is not None
        profile.mandate_start = date.today() + timedelta(days=1)
        profile.mandate_end = profile.mandate_start + timedelta(days=30)
        db.commit()

    payload = {
        "name": "未来委托门禁",
        "package_id": "PKG_A",
        "goal": "首次联络与意愿确认",
        "budget_yuan": 20,
        "case_ids": ["C008"],
        "requested_mode": "channel",
    }
    result = client.post("/api/v1/activities/preflight", headers=headers(role="operator"), json=payload).json()
    assert result["eligible_case_ids"] == []
    assert result["excluded"] == [{"case_id": "C008", "reason": "委托尚未生效"}]
    assert result["production_ready"] is False


def test_channel_activity_freezes_service_versions_after_gate_passes(client: TestClient) -> None:
    enable_channels(client)
    payload = {
        "name": "可执行案件首次联络",
        "package_id": "PKG_A",
        "goal": "首次联络与意愿确认",
        "budget_yuan": 20,
        "case_ids": ["C008"],
        "requested_mode": "channel",
    }
    response = client.post("/api/v1/activities", headers=headers(role="operator"), json=payload)
    assert response.status_code == 201, response.text
    activity = response.json()
    assert activity["mode"] == "channel"
    assert activity["case_ids"] == ["C008"]
    assert activity["service_snapshot"]["agent"]["version"] == 3
    assert activity["service_snapshot"]["self_test_id"].startswith("SELF-")


def test_cross_tenant_case_ids_never_enter_an_activity(client: TestClient) -> None:
    payload = {
        "name": "跨租户尝试",
        "package_id": "PKG_C",
        "goal": "首次联络与意愿确认",
        "budget_yuan": 20,
        "case_ids": ["C008", "C024"],
    }
    result = client.post("/api/v1/activities/preflight", headers=headers("TENANT_B", "operator"), json=payload).json()
    assert result["eligible_case_ids"] == ["C024"]
    assert result["excluded"] == [{"case_id": "C008", "reason": "案件不存在或不属于当前租户资产包"}]


def test_activity_list_and_transitions_are_server_authoritative(client: TestClient) -> None:
    rows = client.get("/api/v1/activities", headers=headers()).json()
    activity = next(row for row in rows if row["status"] == "running")
    paused = client.post(
        f"/api/v1/activities/{activity['activity_id']}/transition",
        headers=headers(role="operator"),
        json={"status": "paused", "reason": "人工检查当前执行证据", "acknowledged": True},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    resumed = client.post(
        f"/api/v1/activities/{activity['activity_id']}/transition",
        headers=headers(role="operator"),
        json={"status": "running", "reason": "检查完成且案件保护状态正常", "acknowledged": True},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "running"

    audit_rows = client.get(
        "/api/v1/governance/audit-events?action_prefix=activity.", headers=headers()
    ).json()
    assert any(row["resource_id"] == activity["activity_id"] for row in audit_rows)
    assert all(row["action"].startswith("activity.") for row in audit_rows)


def test_role_is_resolved_from_membership_not_forged_header(client: TestClient) -> None:
    viewer_headers = headers(role="viewer")
    viewer_headers["X-Role"] = "admin"
    session = client.get("/api/v1/auth/session", headers=viewer_headers)
    assert session.status_code == 200
    assert session.json()["role"] == "viewer"
    denied = client.post("/api/v1/integrations/voice/connection-test", headers=viewer_headers)
    assert denied.status_code == 403


def test_dev_bearer_token_uses_database_membership(client: TestClient) -> None:
    issued = client.post("/api/v1/auth/dev-token", json={"actor_id": "test-operator"})
    assert issued.status_code == 200
    token = issued.json()["access_token"]
    response = client.get(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "TENANT_A"},
    )
    assert response.status_code == 200
    assert response.json()["actor_id"] == "test-operator"
    assert response.json()["role"] == "operator"
    assert response.json()["auth_mode"] == "development-token"


def test_async_connection_test_is_persistent_and_idempotent(client: TestClient) -> None:
    payload = {"idempotency_key": "voice-check-v1"}
    queued = client.post(
        "/api/v1/integrations/voice/connection-test/jobs",
        headers=headers(),
        json=payload,
    )
    assert queued.status_code == 202
    job_id = queued.json()["id"]
    completed = client.get(f"/api/v1/jobs/{job_id}", headers=headers())
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result"]["service_type"] == "voice"
    repeated = client.post(
        "/api/v1/integrations/voice/connection-test/jobs",
        headers=headers(),
        json=payload,
    )
    assert repeated.json()["id"] == job_id
    assert repeated.json()["attempt"] == 1
    listed = client.get("/api/v1/jobs?kind=integration.connection_test", headers=headers()).json()
    assert listed[0]["id"] == job_id
    assert client.get(f"/api/v1/jobs/{job_id}", headers=headers("TENANT_B")).status_code == 404


def test_agent_chatbi_query_persists_answer_sources_and_trace(client: TestClient) -> None:
    session = client.post(
        "/api/v1/agents/sessions",
        headers=headers(role="viewer"),
        json={"scope_type": "global", "title": "经营查询"},
    ).json()
    queued = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
        json={"content": "本月回款和佣金是多少？", "idempotency_key": "chatbi-metrics-1"},
    )
    assert queued.status_code == 202
    job = client.get(f"/api/v1/jobs/{queued.json()['id']}", headers=headers(role="viewer")).json()
    assert job["status"] == "succeeded"
    assert job["result"]["provider"] == "Hermes Agent"
    assert "应计佣金" in job["result"]["answer"]["body"]
    assert len(job["result"]["answer"]["sources"]) == 4
    assert job["result"]["tool_trace"][0]["tool"] == "metrics.query"
    messages = client.get(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
    ).json()
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_agent_write_action_requires_structured_confirmation(client: TestClient) -> None:
    session = client.post(
        "/api/v1/agents/sessions",
        headers=headers(role="operator"),
        json={"scope_type": "activity", "scope_id": "ACT-001", "title": "活动指挥"},
    ).json()
    queued = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="operator"),
        json={"content": "请暂停这个活动", "idempotency_key": "pause-act-001"},
    ).json()
    job = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers(role="operator")).json()
    proposal = job["result"]["proposal"]
    assert proposal["status"] == "pending"
    messages = client.get(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="operator"),
    ).json()
    assert messages[-1]["structured"]["proposal"]["id"] == proposal["id"]
    before = {
        row["activity_id"]: row for row in client.get("/api/v1/activities", headers=headers(role="operator")).json()
    }
    assert before["ACT-001"]["status"] == "running"
    denied = client.post(
        f"/api/v1/agents/proposals/{proposal['id']}/confirm",
        headers=headers(role="viewer"),
        json={"acknowledged": True},
    )
    assert denied.status_code == 403
    confirmed = client.post(
        f"/api/v1/agents/proposals/{proposal['id']}/confirm",
        headers=headers(role="operator"),
        json={"acknowledged": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    after = {
        row["activity_id"]: row for row in client.get("/api/v1/activities", headers=headers(role="operator")).json()
    }
    assert after["ACT-001"]["status"] == "paused"


def test_agent_sessions_are_tenant_scoped(client: TestClient) -> None:
    session = client.post(
        "/api/v1/agents/sessions",
        headers=headers("TENANT_A", "viewer"),
        json={"scope_type": "global", "title": "租户隔离"},
    ).json()
    response = client.get(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers("TENANT_B", "viewer"),
    )
    assert response.status_code == 404


def test_deepseek_harness_contract_resumes_session_and_blocks_dangerous_tools(client: TestClient) -> None:
    saved = client.put(
        "/api/v1/integrations/agent",
        headers=headers(),
        json={
            "provider": "DeepSeek Harness",
            "settings": {
                "endpoint": "stdio://deepseek-harness-sdk",
                "profile": "fulfillops-safe",
                "approval": "高影响动作需确认",
                "transport": "sandbox-contract",
                "safetyPreset": "fulfillops-safe",
                "sessionPersistence": "database-checkpoint",
            },
            "credential": "harness-contract-secret",
        },
    )
    assert saved.status_code == 200, saved.text
    tested = client.post(
        "/api/v1/integrations/agent/connection-test/jobs",
        headers=headers(),
        json={"idempotency_key": "dsh-contract-test"},
    ).json()
    job = client.get(f"/api/v1/jobs/{tested['id']}", headers=headers()).json()
    assert job["status"] == "succeeded"
    assert "8/8" in job["result"]["detail"]
    gateway = client.get("/api/v1/agents/gateway", headers=headers(role="viewer")).json()
    assert gateway["provider"] == "DeepSeek Harness"
    assert gateway["transport"] == "sandbox-contract"
    assert len(gateway["tools"]) == 8
    assert "shell" in gateway["forbidden_tools"]

    session = client.post(
        "/api/v1/agents/sessions",
        headers=headers(role="viewer"),
        json={"scope_type": "global", "title": "Harness 经营查询"},
    ).json()
    first = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
        json={"content": "查询 C002 当前状态", "idempotency_key": "dsh-turn-1"},
    ).json()
    first_job = client.get(f"/api/v1/jobs/{first['id']}", headers=headers(role="viewer")).json()
    assert first_job["result"]["provider"] == "DeepSeek Harness"
    assert first_job["result"]["runtime"]["resumed"] is False
    provider_session_id = first_job["result"]["runtime"]["provider_session_id"]

    second = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
        json={"content": "本月回款和佣金是多少？", "idempotency_key": "dsh-turn-2"},
    ).json()
    second_job = client.get(f"/api/v1/jobs/{second['id']}", headers=headers(role="viewer")).json()
    runtime = second_job["result"]["runtime"]
    assert runtime["resumed"] is True
    assert runtime["turn_count"] == 2
    assert runtime["provider_session_id"] == provider_session_id
    assert runtime["event_cursor"] > first_job["result"]["runtime"]["event_cursor"]

    denied = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
        json={"content": "请用 shell 直接修改账务", "idempotency_key": "dsh-turn-denied"},
    ).json()
    denied_job = client.get(f"/api/v1/jobs/{denied['id']}", headers=headers(role="viewer")).json()
    assert denied_job["result"]["tool_trace"][-1]["status"] == "blocked"
    assert denied_job["result"]["proposal"] is None

    checkpoint = client.get(
        f"/api/v1/agents/sessions/{session['id']}/runtime",
        headers=headers(role="viewer"),
    )
    assert checkpoint.status_code == 200
    assert checkpoint.json()["turn_count"] == 3
    assert checkpoint.json()["provider_session_id"] == provider_session_id
    assert (
        client.get(
            f"/api/v1/agents/sessions/{session['id']}/runtime",
            headers=headers("TENANT_B", "viewer"),
        ).status_code
        == 404
    )


def test_deepseek_harness_sdk_mode_does_not_fake_a_successful_connection(client: TestClient) -> None:
    response = client.put(
        "/api/v1/integrations/agent",
        headers=headers(),
        json={
            "provider": "DeepSeek Harness",
            "settings": {
                "endpoint": "stdio://deepseek-harness-sdk",
                "profile": "fulfillops-safe",
                "approval": "高影响动作需确认",
                "transport": "python-sdk",
                "safetyPreset": "fulfillops-safe",
                "sessionPersistence": "runtime-jsonl",
            },
            "credential": "sdk-not-installed-secret",
        },
    )
    assert response.status_code == 200
    queued = client.post(
        "/api/v1/integrations/agent/connection-test/jobs",
        headers=headers(),
        json={"idempotency_key": "dsh-sdk-real-test"},
    ).json()
    job = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers()).json()
    assert job["status"] == "failed"
    assert "SDK" in job["error"] or "安全插件" in job["error"] or "未显式启用" in job["error"]
    current = client.get("/api/v1/integrations", headers=headers()).json()
    assert current["services"]["agent"]["connected"] is False


def test_runtime_failure_persists_failed_agent_run(client: TestClient) -> None:
    saved = client.put(
        "/api/v1/integrations/agent",
        headers=headers(),
        json={
            "provider": "DeepSeek Harness",
            "settings": {
                "endpoint": "stdio://deepseek-harness-sdk",
                "profile": "fulfillops-safe",
                "approval": "高影响动作需确认",
                "transport": "python-sdk",
                "safetyPreset": "fulfillops-safe",
                "sessionPersistence": "runtime-jsonl",
            },
            "credential": "sdk-disabled-secret",
        },
    )
    assert saved.status_code == 200
    session = client.post(
        "/api/v1/agents/sessions",
        headers=headers(role="viewer"),
        json={"scope_type": "global", "title": "失败恢复"},
    ).json()
    queued = client.post(
        f"/api/v1/agents/sessions/{session['id']}/messages",
        headers=headers(role="viewer"),
        json={"content": "查询 C002", "idempotency_key": "sdk-disabled-turn"},
    ).json()
    job = client.get(f"/api/v1/jobs/{queued['id']}", headers=headers(role="viewer")).json()
    assert job["status"] == "failed"
    with client.app.state.Session() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.job_id == job["id"]))
        assert run is not None
        assert run.status == "failed"
        assert "未显式启用" in run.error
