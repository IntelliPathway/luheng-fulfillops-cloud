from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .version import APP_VERSION

MCP_PROTOCOL_VERSION = "2025-06-18"
PUBLIC_TO_INTERNAL = {
    "case_read": "case.read",
    "activity_read": "activity.read",
    "metrics_query": "metrics.query",
    "policy_read": "policy.read",
    "knowledge_search": "knowledge.search",
    "run_read": "run.read",
    "activity_pause_propose": "activity.pause.propose",
    "activity_resume_propose": "activity.resume.propose",
}


def _tool(name: str, title: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


MCP_TOOLS = [
    _tool(
        "case_read",
        "读取案件",
        "读取当前租户案件事实与保护状态。",
        {"case_id": {"type": "string", "pattern": "^C[0-9]{3,12}$"}},
        ["case_id"],
    ),
    _tool(
        "activity_read",
        "读取活动",
        "读取活动状态、范围与服务快照。",
        {"activity_id": {"type": "string", "pattern": "^ACT-[0-9]{3,12}$"}},
        ["activity_id"],
    ),
    _tool("metrics_query", "查询经营指标", "查询带口径与来源的回款和佣金指标。", {}, []),
    _tool("policy_read", "读取策略", "读取当前租户已登记的授权策略版本。", {"package_id": {"type": "string"}}, []),
    _tool(
        "knowledge_search",
        "检索知识",
        "检索租户策略与操作知识并返回版本化来源。",
        {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
        ["query"],
    ),
    _tool("run_read", "读取运行", "读取当前租户 Agent 运行与工具结果。", {"run_id": {"type": "string"}}, ["run_id"]),
    _tool(
        "activity_pause_propose",
        "生成暂停提案",
        "仅生成活动暂停提案，不直接修改活动。",
        {"activity_id": {"type": "string", "pattern": "^ACT-[0-9]{3,12}$"}},
        ["activity_id"],
    ),
    _tool(
        "activity_resume_propose",
        "生成恢复提案",
        "仅生成活动恢复提案，不直接修改活动。",
        {"activity_id": {"type": "string", "pattern": "^ACT-[0-9]{3,12}$"}},
        ["activity_id"],
    ),
]


def _runtime_environment() -> tuple[str, str]:
    base_url = os.getenv("FULFILLOPS_MCP_BASE_URL", "").rstrip("/")
    token = os.getenv("FULFILLOPS_MCP_TOKEN", "")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("FULFILLOPS_MCP_BASE_URL 必须是明确的 HTTP(S) 内部服务地址")
    if not token:
        raise RuntimeError("FULFILLOPS_MCP_TOKEN 未配置")
    return base_url, token


def call_fulfillops_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    internal_name = PUBLIC_TO_INTERNAL.get(tool_name)
    if not internal_name:
        raise ValueError("工具未注册")
    base_url, token = _runtime_environment()
    request = Request(
        f"{base_url}/api/v1/internal/agent-tools/{quote(internal_name, safe='.')}",
        data=json.dumps({"arguments": arguments}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"FulfillOps 工具服务拒绝请求（{exc.code}）：{detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("FulfillOps 工具服务暂时不可用") from exc
    if not isinstance(payload, dict):
        raise TypeError("FulfillOps 工具返回格式无效")
    return payload


def _result(request_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_request(
    frame: dict[str, Any],
    caller: Callable[[str, dict[str, Any]], dict[str, Any]] = call_fulfillops_tool,
) -> dict[str, Any] | None:
    request_id = frame.get("id")
    method = frame.get("method")
    params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
    if request_id is None:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        protocol_version = requested if isinstance(requested, str) and requested else MCP_PROTOCOL_VERSION
        return _result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "luheng-fulfillops-safe-tools", "version": APP_VERSION},
                "instructions": "仅允许租户范围内只读查询与活动暂停/恢复提案；所有业务写入由 FulfillOps API 复核。",
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": MCP_TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name not in PUBLIC_TO_INTERNAL:
            return _error(request_id, -32602, "工具未注册")
        try:
            value = caller(name, arguments)
        except (TypeError, ValueError, RuntimeError) as exc:
            return _result(
                request_id,
                {"content": [{"type": "text", "text": str(exc)[:1000]}], "isError": True},
            )
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
                "structuredContent": value,
                "isError": False,
            },
        )
    return _error(request_id, -32601, "Method not found")


def serve() -> None:
    for raw_line in sys.stdin:
        try:
            frame = json.loads(raw_line)
            if not isinstance(frame, dict):
                raise TypeError("JSON-RPC frame must be an object")
            response = handle_request(frame)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            response = _error(None, -32700, str(exc)[:300])
        # The MCP process boundary must convert every unexpected failure into a
        # JSON-RPC error while keeping stdout free of logs.
        except Exception as exc:  # noqa: BLE001
            print(f"fulfillops-mcp: {type(exc).__name__}: {str(exc)[:500]}", file=sys.stderr, flush=True)
            response = _error(frame.get("id") if isinstance(frame, dict) else None, -32603, "Internal error")
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    serve()
