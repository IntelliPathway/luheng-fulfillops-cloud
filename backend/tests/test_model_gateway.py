from __future__ import annotations

import base64
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

import app.model_replay as replay_module
from app.main import create_app
from app.migrations import run_sqlite_compatibility_migrations
from app.model_gateway import (
    ModelGatewayError,
    ModelInvocation,
    invoke_json_model,
    model_gateway_health,
    model_policy,
)
from app.models import AuditEvent, ModelReplayRun, ServiceConfig
from app.secret_store import store_secret


def _config(settings: dict | None = None) -> ServiceConfig:
    return ServiceConfig(
        tenant_id="TENANT_A",
        service_type="model",
        provider="DeepSeek",
        settings=settings
        or {
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "timeout": "20",
            "executionMode": "live-provider",
            "maxOutputTokens": "256",
            "maxCostUsd": "0.05",
        },
        secret_ref="secret://test",
        credential_last4="test",
        version=4,
        connected=True,
    )


def test_model_policy_fails_closed_for_endpoint_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.delenv("MODEL_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.delenv("MODEL_EGRESS_ALLOWLIST", raising=False)
    monkeypatch.delenv("MODEL_ALLOWED_MODELS", raising=False)

    with pytest.raises(ModelGatewayError, match="HTTPS"):
        model_policy(_config({"endpoint": "http://api.deepseek.com", "model": "deepseek-flash", "executionMode": "contract-only", "timeout": "20"}))
    with pytest.raises(ModelGatewayError, match="本地或私有"):
        model_policy(_config({"endpoint": "https://169.254.169.254", "model": "deepseek-flash", "executionMode": "contract-only", "timeout": "20"}))
    with pytest.raises(ModelGatewayError, match="白名单"):
        model_policy(_config({"endpoint": "https://model.attacker.test", "model": "deepseek-flash", "executionMode": "contract-only", "timeout": "20"}))
    with pytest.raises(ModelGatewayError, match="模型名称"):
        model_policy(_config({"endpoint": "https://api.deepseek.com", "model": "retired-model", "executionMode": "live-provider", "timeout": "20"}))


def test_json_model_call_enforces_schema_budget_and_safe_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_LIVE_MODEL_CALLS", "true")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["stream"] is False
        assert payload["user_id"].startswith("tenant-")
        assert "TENANT_A" not in payload["user_id"]
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-sensitive-request-id"},
            json={
                "choices": [{"message": {"content": '{"verdict":"pass","reviewed_cases":[],"risk_flags":[]}'}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    result = invoke_json_model(
        _config(),
        "provider-secret-never-persisted",
        "TENANT_A",
        "Return JSON.",
        "Evaluate synthetic evidence.",
        transport=httpx.MockTransport(handler),
    )
    assert result.content["verdict"] == "pass"
    assert result.input_tokens == 120
    assert result.output_tokens == 40
    assert result.estimated_cost_usd == 0.0032
    assert len(result.request_digest) == 64
    assert len(result.response_digest) == 64
    assert len(result.provider_request_id_hash or "") == 16
    assert seen["request"].headers["authorization"] == "Bearer provider-secret-never-persisted"
    assert "provider-secret" not in str(result)


def test_model_gateway_sanitizes_provider_errors_and_rejects_empty_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_LIVE_MODEL_CALLS", "true")

    def limited(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "secret upstream detail"}})

    with pytest.raises(ModelGatewayError) as captured:
        invoke_json_model(_config(), "secret-key", "TENANT_A", "Return JSON.", "test", transport=httpx.MockTransport(limited))
    assert captured.value.code == "rate_limited"
    assert captured.value.retriable is True
    assert "secret upstream detail" not in str(captured.value)

    def empty(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}], "usage": {}})

    with pytest.raises(ModelGatewayError) as captured:
        invoke_json_model(_config(), "secret-key", "TENANT_A", "Return JSON.", "test", transport=httpx.MockTransport(empty))
    assert captured.value.code == "invalid_response"


def test_contract_health_never_claims_live_provider_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_LIVE_MODEL_CALLS", raising=False)
    config = _config({
        "endpoint": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "timeout": "30",
        "executionMode": "contract-only",
    })
    health = model_gateway_health(config)
    assert health["status"] == "contract"
    assert health["endpoint_host"] == "api.deepseek.com"
    assert health["live_calls_enabled"] is False
    assert "不会发起外部请求" in health["detail"]


def test_existing_sqlite_replay_table_gets_additive_v08_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'v07.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE model_replay_runs (id varchar(40) PRIMARY KEY)")
    applied = run_sqlite_compatibility_migrations(engine)
    assert set(applied) == {
        "model_config_version",
        "model_name",
        "policy_snapshot",
        "external_call_count",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
    }
    assert set(applied) <= {column["name"] for column in inspect(engine).get_columns("model_replay_runs")}
    assert run_sqlite_compatibility_migrations(engine) == []
    engine.dispose()


def test_live_replay_requires_deployment_gate_and_explicit_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app("sqlite:///:memory:")
    headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
    with TestClient(app) as client:
        disabled = client.post(
            "/api/v1/agents/replays/jobs",
            headers=headers,
            json={"mode": "live-provider", "acknowledged_external_call": True},
        )
        assert disabled.status_code == 409
        monkeypatch.setenv("ENABLE_LIVE_MODEL_CALLS", "true")
        missing_ack = client.post(
            "/api/v1/agents/replays/jobs",
            headers=headers,
            json={"mode": "live-provider", "acknowledged_external_call": False},
        )
        assert missing_ack.status_code == 422


def test_live_replay_records_usage_and_digests_without_raw_model_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_LIVE_MODEL_CALLS", "true")
    monkeypatch.setenv("SECRET_STORE_BACKEND", "local-envelope")
    monkeypatch.setenv("SECRET_MASTER_KEY_VERSION", "replay-v1")
    monkeypatch.setenv("SECRET_MASTER_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        config = db.scalar(
            select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "model")
        )
        reference, last4 = store_secret(db, "TENANT_A", "model", "live-replay-secret")
        config.secret_ref = reference
        config.credential_last4 = last4
        config.settings = {
            "endpoint": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "timeout": "20",
            "executionMode": "live-provider",
            "maxOutputTokens": "256",
            "maxCostUsd": "0.05",
        }
        config.version = 8
        config.connected = True
        db.commit()

    def fake_invocation(*_args, **_kwargs) -> ModelInvocation:
        return ModelInvocation(
            content={
                "verdict": "pass",
                "reviewed_cases": ["money-metrics", "protected-case", "pause-proposal", "forbidden-shell"],
                "risk_flags": [],
                "unpersisted_summary": "raw-provider-text-must-not-be-stored",
            },
            input_tokens=240,
            output_tokens=32,
            estimated_cost_usd=0.00544,
            latency_ms=188,
            request_digest="a" * 64,
            response_digest="b" * 64,
            provider_request_id_hash="c" * 16,
        )

    monkeypatch.setattr(replay_module, "invoke_json_model", fake_invocation)
    headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
    with TestClient(app) as client:
        queued = client.post(
            "/api/v1/agents/replays/jobs",
            headers=headers,
            json={
                "mode": "live-provider",
                "acknowledged_external_call": True,
                "idempotency_key": "live-provider-safe-1",
            },
        )
        assert queued.status_code == 202, queued.text
        job = client.get(f"/api/v1/jobs/{queued.json()['id']}", headers=headers).json()
        assert job["status"] == "succeeded"
        assert job["result"]["status"] == "passed"
        assert job["result"]["external_call_count"] == 1
        replay = client.get(f"/api/v1/agents/replays/{job['result']['replay_run_id']}", headers=headers).json()
        assert replay["passed_count"] == 5
        assert replay["input_tokens"] == 240
        assert replay["output_tokens"] == 32
        assert replay["estimated_cost_usd"] == 0.00544
        assert replay["policy_snapshot"]["data_policy"] == "synthetic-or-redacted-only"
        assert replay["results"][-1]["request_digest"] == "a" * 64
        assert "raw-provider-text-must-not-be-stored" not in str(replay)
        with app.state.Session() as db:
            stored = db.get(ModelReplayRun, replay["id"])
            audit = db.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == stored.id,
                    AuditEvent.action == "agent.replay.completed",
                )
            )
            assert "raw-provider-text-must-not-be-stored" not in str(audit.detail)
