from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .domain import utcnow
from .models import AuditEvent, ModelReplayRun
from .runtime_adapters import RuntimeContext, adapter_for

REPLAY_ROOT = Path(__file__).resolve().parents[1] / "replay"
SUPPORTED_REPLAY_SUITES = {"fulfillops-safe-core": REPLAY_ROOT / "fulfillops-safe-core.v1.json"}


class ReplaySuiteError(RuntimeError):
    pass


def load_replay_suite(name: str) -> tuple[dict[str, Any], str]:
    path = SUPPORTED_REPLAY_SUITES.get(name)
    if not path:
        raise ReplaySuiteError(f"不支持的回放套件：{name}")
    raw = path.read_bytes()
    try:
        suite = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplaySuiteError(f"回放套件 {name} 不是有效 JSON") from exc
    if suite.get("name") != name or suite.get("mode") != "deterministic-contract":
        raise ReplaySuiteError(f"回放套件 {name} 元数据无效")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ReplaySuiteError(f"回放套件 {name} 没有测试用例")
    return suite, hashlib.sha256(raw).hexdigest()


def replay_suite_metadata(name: str) -> dict[str, str]:
    suite, digest = load_replay_suite(name)
    return {
        "name": str(suite["name"]),
        "version": str(suite["version"]),
        "mode": str(suite["mode"]),
        "dataset_digest": digest,
        "provider": "FulfillOps Sandbox",
        "profile": "fulfillops-safe-core-v1",
    }


def _case_result(db: Session, replay: ModelReplayRun, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "")
    query = str(case.get("query") or "")
    if not case_id or not query:
        raise ReplaySuiteError("回放用例缺少 id 或 query")
    context = RuntimeContext(
        tenant_id=replay.tenant_id,
        session_id=f"{replay.id}:{case_id}",
        scope_type=str(case.get("scope_type") or "global"),
        scope_id=str(case["scope_id"]) if case.get("scope_id") else None,
        provider=replay.provider,
        profile=replay.profile,
        settings={
            "transport": "sandbox-contract",
            "safetyPreset": "fulfillops-safe",
            "sessionPersistence": "replay-isolated",
        },
        logical_provider_session_id=f"REPLAY-{case_id}",
        prior_turn_count=0,
        previous_metadata={},
        replay_messages=[],
    )
    generated = adapter_for(replay.provider).run(db, context, query)
    checks: list[dict[str, Any]] = []
    trace = {str(row.get("tool")): str(row.get("status")) for row in generated.get("tool_trace") or []}
    for tool, expected_status in dict(case.get("required_tools") or {}).items():
        actual = trace.get(str(tool))
        checks.append(
            {
                "name": f"tool:{tool}",
                "passed": actual == expected_status,
                "expected": expected_status,
                "actual": actual,
            }
        )
    answer = generated.get("answer") or {}
    searchable_answer = "\n".join(str(answer.get(key) or "") for key in ("title", "body"))
    for term in case.get("required_answer_terms") or []:
        checks.append(
            {
                "name": f"answer:{term}",
                "passed": str(term) in searchable_answer,
                "expected": "present",
                "actual": "present" if str(term) in searchable_answer else "missing",
            }
        )
    expected_proposal = case.get("expected_proposal_action")
    proposal = generated.get("proposal")
    actual_proposal = proposal.get("action_type") if isinstance(proposal, dict) else None
    checks.append(
        {
            "name": "proposal_action",
            "passed": actual_proposal == expected_proposal,
            "expected": expected_proposal,
            "actual": actual_proposal,
        }
    )
    required_evidence = case.get("required_evidence")
    if isinstance(required_evidence, dict):
        evidence_matches = any(
            isinstance(row, dict) and all(row.get(key) == value for key, value in required_evidence.items())
            for row in generated.get("evidence") or []
        )
        checks.append(
            {
                "name": "evidence",
                "passed": evidence_matches,
                "expected": required_evidence,
                "actual": "matched" if evidence_matches else "missing",
            }
        )
    has_sources = bool(answer.get("sources"))
    checks.append(
        {
            "name": "sources",
            "passed": has_sources,
            "expected": "non-empty",
            "actual": len(answer.get("sources") or []),
        }
    )
    canonical_output = json.dumps(generated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "case_id": case_id,
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks": checks,
        "tool_trace": generated.get("tool_trace") or [],
        "answer_title": str(answer.get("title") or ""),
        "output_digest": hashlib.sha256(canonical_output.encode()).hexdigest(),
    }


def execute_model_replay(db: Session, replay: ModelReplayRun) -> dict[str, Any]:
    suite, digest = load_replay_suite(replay.suite_name)
    if digest != replay.dataset_digest or str(suite.get("version")) != replay.suite_version:
        raise ReplaySuiteError("回放数据集与入队时的版本或摘要不一致")
    if replay.mode != "deterministic-contract":
        raise ReplaySuiteError("当前版本只允许不调用外部模型的 deterministic-contract 回放")
    replay.status = "running"
    results: list[dict[str, Any]] = []
    for case in suite["cases"]:
        try:
            results.append(_case_result(db, replay, case))
        except Exception as exc:  # noqa: BLE001 - record case isolation without leaking provider data
            results.append(
                {
                    "case_id": str(case.get("id") or "unknown"),
                    "status": "failed",
                    "checks": [
                        {
                            "name": "execution",
                            "passed": False,
                            "expected": "completed",
                            "actual": type(exc).__name__,
                        }
                    ],
                    "tool_trace": [],
                    "answer_title": "",
                    "output_digest": None,
                }
            )
    replay.results = results
    replay.passed_count = sum(1 for result in results if result["status"] == "passed")
    replay.failed_count = len(results) - replay.passed_count
    replay.status = "passed" if replay.failed_count == 0 else "failed"
    replay.completed_at = utcnow()
    db.add(
        AuditEvent(
            tenant_id=replay.tenant_id,
            actor_id=replay.created_by,
            action="agent.replay.completed",
            resource_type="model_replay_run",
            resource_id=replay.id,
            detail={
                "suite_name": replay.suite_name,
                "suite_version": replay.suite_version,
                "dataset_digest": replay.dataset_digest,
                "status": replay.status,
                "passed_count": replay.passed_count,
                "failed_count": replay.failed_count,
                "mode": replay.mode,
            },
        )
    )
    return {
        "replay_run_id": replay.id,
        "status": replay.status,
        "suite_name": replay.suite_name,
        "suite_version": replay.suite_version,
        "mode": replay.mode,
        "dataset_digest": replay.dataset_digest,
        "passed_count": replay.passed_count,
        "failed_count": replay.failed_count,
        "completed_at": replay.completed_at.isoformat(),
    }
