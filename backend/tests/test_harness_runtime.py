from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.harness_mcp import MCP_TOOLS, handle_request
from app.harness_runtime import (
    HarnessLaunchContext,
    HarnessRuntimeManager,
    project_run_result,
)
from app.main import create_app
from app.security import issue_runtime_token


def test_mcp_server_exposes_only_the_safe_tool_catalog() -> None:
    initialized = handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    )
    assert initialized["result"]["serverInfo"]["name"] == "luheng-fulfillops-safe-tools"
    listed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert listed["result"]["tools"] == MCP_TOOLS
    assert {item["name"] for item in MCP_TOOLS} == {
        "case_read",
        "activity_read",
        "metrics_query",
        "policy_read",
        "knowledge_search",
        "run_read",
        "activity_pause_propose",
        "activity_resume_propose",
    }
    assert handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_tool_call_returns_structured_content_without_executing_unknown_tools() -> None:
    seen = []

    def caller(name: str, arguments: dict) -> dict:
        seen.append((name, arguments))
        return {"answer": {"title": "案件查询", "body": "ok"}, "evidence": [{"case_id": "C002"}]}

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "case_read", "arguments": {"case_id": "C002"}},
        },
        caller,
    )
    assert seen == [("case_read", {"case_id": "C002"})]
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["evidence"][0]["case_id"] == "C002"
    unknown = handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "shell", "arguments": {}}},
        caller,
    )
    assert unknown["error"]["code"] == -32602


def test_internal_tool_endpoint_is_bound_to_runtime_tenant_and_session() -> None:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/agents/sessions",
            headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"},
            json={"scope_type": "global", "title": "Runtime tool test"},
        ).json()
        token, _ = issue_runtime_token("TENANT_A", session["id"], "global", None)
        authorized = client.post(
            "/api/v1/internal/agent-tools/case.read",
            headers={"Authorization": f"Bearer {token}"},
            json={"arguments": {"case_id": "C002"}},
        )
        assert authorized.status_code == 200
        assert authorized.json()["evidence"][0]["case_id"] == "C002"
        cross_tenant = client.post(
            "/api/v1/internal/agent-tools/case.read",
            headers={"Authorization": f"Bearer {token}"},
            json={"arguments": {"case_id": "C024"}},
        )
        assert cross_tenant.status_code == 200
        assert cross_tenant.json()["evidence"] == []
        forbidden = client.post(
            "/api/v1/internal/agent-tools/payment.write",
            headers={"Authorization": f"Bearer {token}"},
            json={"arguments": {}},
        )
        assert forbidden.status_code == 422

        case_session = client.post(
            "/api/v1/agents/sessions",
            headers={"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"},
            json={"scope_type": "case", "scope_id": "C002", "title": "Scoped Runtime tool test"},
        ).json()
        case_token, _ = issue_runtime_token("TENANT_A", case_session["id"], "case", "C002")
        outside_scope = client.post(
            "/api/v1/internal/agent-tools/case.read",
            headers={"Authorization": f"Bearer {case_token}"},
            json={"arguments": {"case_id": "C003"}},
        )
        assert outside_scope.status_code == 422
        activity_proposal = client.post(
            "/api/v1/internal/agent-tools/activity.pause.propose",
            headers={"Authorization": f"Bearer {case_token}"},
            json={"arguments": {"activity_id": "ACT-001"}},
        )
        assert activity_proposal.status_code == 422


def test_project_run_result_keeps_verified_tool_sources_and_proposal() -> None:
    tool_value = {
        "answer": {
            "title": "已生成暂停提案",
            "body": "deterministic body",
            "facts": [{"label": "活动", "value": "ACT-001"}],
            "sources": [{"label": "活动状态", "entity_type": "activity", "entity_id": "ACT-001", "version": "current"}],
            "navigation_hint": "agent:ACT-001",
        },
        "evidence": [{"activity_id": "ACT-001", "status": "running"}],
        "proposal": {
            "action_type": "activity.pause",
            "arguments": {"activity_id": "ACT-001", "target_status": "paused"},
            "required_role": "operator",
        },
    }
    result = SimpleNamespace(
        final_response="已核验活动状态，并生成暂停提案。",
        finish_reason="completed",
        events=[
            {
                "type": "tool/call",
                "data": {
                    "callId": "call-1",
                    "name": "mcp__fulfillops__activity_pause_propose",
                    "arguments": '{"activity_id":"ACT-001"}',
                },
            },
            {
                "type": "tool/result",
                "data": {
                    "message": {
                        "source": {"kind": "tool", "callId": "call-1"},
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "call-1",
                                "content": [{"type": "text", "text": json.dumps(tool_value, ensure_ascii=False)}],
                                "isError": False,
                            }
                        ],
                    }
                },
            },
        ],
    )
    generated, metadata = project_run_result(result)
    assert generated["answer"]["body"] == result.final_response
    assert generated["tool_trace"] == [
        {
            "tool": "activity.pause.propose",
            "status": "completed",
            "detail": "工具结果已由 FulfillOps API 在租户范围内生成",
        }
    ]
    assert generated["proposal"]["action_type"] == "activity.pause"
    assert metadata == {"sdk_event_count": 2, "verified_tool_count": 1, "finish_reason": "completed"}


def test_runtime_manager_reuses_process_then_replays_database_checkpoint(monkeypatch, tmp_path) -> None:
    instances = []

    class FakeSdk:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.prompts = []
            self.closed = False
            instances.append(self)

        def start(self) -> None:
            return None

        def run(self, prompt: str, *, session_id: str):
            self.prompts.append((prompt, session_id))
            return SimpleNamespace(final_response="ok", finish_reason="completed", events=[])

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=FakeSdk))
    monkeypatch.setenv("FULFILLOPS_ENABLE_DSH_RUNTIME", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("FULFILLOPS_DSH_ROOT", str(tmp_path / "runtime"))
    settings = {
        "transport": "python-sdk",
        "safetyPreset": "fulfillops-safe",
        "modelService": {"provider": "DeepSeek", "model": "deepseek-chat", "endpoint": "https://api.deepseek.test/v1"},
    }
    manager = HarnessRuntimeManager(lambda: FakeSdk)
    first_context = HarnessLaunchContext(
        tenant_id="TENANT_A",
        session_id="ASES-1",
        scope_type="global",
        scope_id=None,
        settings=settings,
        logical_provider_session_id="DSH-LOGICAL",
        prior_turn_count=0,
        previous_metadata={},
        replay_messages=[],
    )
    _, first = manager.run(first_context, "first")
    assert first["resume_mode"] == "new"
    assert first["process_generation"] == 1
    second_context = HarnessLaunchContext(
        **{
            **first_context.__dict__,
            "prior_turn_count": 1,
            "previous_metadata": {"process_generation": 1},
            "replay_messages": [{"role": "user", "content": "first"}, {"role": "assistant", "content": "ok"}],
        }
    )
    _, second = manager.run(second_context, "second")
    assert second["resume_mode"] == "in-process"
    assert len(instances) == 1
    assert instances[0].prompts[-1] == ("second", "DSH-LOGICAL")

    manager.close_all()
    _, recovered = manager.run(
        HarnessLaunchContext(
            **{
                **first_context.__dict__,
                "prior_turn_count": 2,
                "previous_metadata": {"process_generation": 1},
                "replay_messages": [{"role": "user", "content": "first"}, {"role": "assistant", "content": "ok"}],
            }
        ),
        "after restart",
    )
    assert recovered["resume_mode"] == "checkpoint-replay"
    assert recovered["process_generation"] == 2
    assert recovered["provider_session_id"] == "DSH-LOGICAL-r2"
    assert "[历史检查点]" in instances[-1].prompts[-1][0]
    assert instances[-1].prompts[-1][1] == "DSH-LOGICAL-r2"
    manager.close_all()


def test_python_sdk_mode_runs_through_persistent_agent_job(monkeypatch, tmp_path) -> None:
    class FakeSdk:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False

        def start(self) -> None:
            return None

        def run(self, prompt: str, *, session_id: str):
            value = {
                "answer": {
                    "title": "C002 案件查询",
                    "body": "案件处于履约中。",
                    "facts": [{"label": "保护状态", "value": "正常"}],
                    "sources": [
                        {"label": "案件主数据", "entity_type": "case", "entity_id": "C002", "version": "current"}
                    ],
                    "navigation_hint": "case:C002",
                },
                "evidence": [{"case_id": "C002", "status": "履约中", "blocked": False}],
                "proposal": None,
            }
            return SimpleNamespace(
                final_response="C002 当前处于履约中，保护状态正常。",
                finish_reason="completed",
                events=[
                    {
                        "type": "tool/call",
                        "data": {"callId": "call-case", "name": "mcp__fulfillops__case_read", "arguments": "{}"},
                    },
                    {
                        "type": "tool/result",
                        "data": {
                            "message": {
                                "source": {"kind": "tool", "callId": "call-case"},
                                "content": [
                                    {
                                        "type": "tool-result",
                                        "toolCallId": "call-case",
                                        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                                        "isError": False,
                                    }
                                ],
                            }
                        },
                    },
                ],
            )

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(DeepSeekHarness=FakeSdk))
    monkeypatch.setenv("FULFILLOPS_ENABLE_DSH_RUNTIME", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("FULFILLOPS_DSH_ROOT", str(tmp_path / "runtime"))
    app = create_app("sqlite:///:memory:")
    admin_headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
    viewer_headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/integrations/agent",
            headers=admin_headers,
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
                "credential": "runtime-secret-reference",
            },
        )
        assert saved.status_code == 200
        connection = client.post(
            "/api/v1/integrations/agent/connection-test/jobs",
            headers=admin_headers,
            json={"idempotency_key": "real-sdk-contract"},
        ).json()
        connection_job = client.get(f"/api/v1/jobs/{connection['id']}", headers=admin_headers).json()
        assert connection_job["status"] == "succeeded"
        assert "8 个受控 MCP 工具" in connection_job["result"]["detail"]

        session = client.post(
            "/api/v1/agents/sessions",
            headers=viewer_headers,
            json={"scope_type": "global", "title": "SDK job"},
        ).json()
        queued = client.post(
            f"/api/v1/agents/sessions/{session['id']}/messages",
            headers=viewer_headers,
            json={"content": "查询 C002 当前状态", "idempotency_key": "sdk-job-turn-1"},
        ).json()
        job = client.get(f"/api/v1/jobs/{queued['id']}", headers=viewer_headers).json()
        assert job["status"] == "succeeded"
        assert job["result"]["tool_trace"][0]["tool"] == "case.read"
        assert job["result"]["runtime"]["sdk_protocol"] == "json-rpc/stdio"
        assert job["result"]["runtime"]["resume_mode"] == "new"
        assert job["result"]["runtime"]["verified_tool_count"] == 1
        runtime = client.get(f"/api/v1/agents/sessions/{session['id']}/runtime", headers=viewer_headers).json()
        assert runtime["turn_count"] == 1
        assert runtime["runtime_metadata"]["process_generation"] == 1
