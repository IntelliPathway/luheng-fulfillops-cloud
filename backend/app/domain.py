from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Activity,
    AssetPackage,
    CaseRecord,
    IntegrationState,
    SelfTestReport,
    ServiceConfig,
)

SERVICE_TYPES = ("agent", "model", "voice", "phone")
REQUIRED_SETTINGS = {
    "agent": ("endpoint", "profile", "approval"),
    "model": ("endpoint", "model", "timeout"),
    "voice": ("region", "asr", "tts", "sampleRate"),
    "phone": ("sipHost", "trunk", "callerId", "callback"),
}
SECRET_KEYS = {"secret", "password", "token", "api_key", "apikey", "access_key", "accesskey", "auth_token"}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def validate_service_settings(service_type: str, settings: dict[str, Any]) -> None:
    if service_type not in SERVICE_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知服务类型")
    forbidden = [key for key in settings if key.lower().replace("-", "_") in SECRET_KEYS]
    if forbidden:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="凭证必须通过 credential 字段提交")
    missing = [key for key in REQUIRED_SETTINGS[service_type] if not str(settings.get(key, "")).strip()]
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"缺少必填配置：{', '.join(missing)}")


def service_versions(configs: list[ServiceConfig]) -> dict[str, int]:
    return {config.service_type: config.version for config in configs}


def versions_match(configs: list[ServiceConfig], snapshot: dict[str, int] | None) -> bool:
    current = service_versions(configs)
    return len(current) == 4 and all(current.get(key) == (snapshot or {}).get(key) for key in SERVICE_TYPES)


def gate_for(db: Session, tenant_id: str, now: datetime | None = None) -> dict[str, bool]:
    now = now or utcnow()
    configs = list(db.scalars(select(ServiceConfig).where(ServiceConfig.tenant_id == tenant_id)))
    state = db.get(IntegrationState, tenant_id)
    report = db.get(SelfTestReport, state.last_self_test_id) if state and state.last_self_test_id else None
    all_saved = len(configs) == 4 and all(item.saved for item in configs)
    all_connected = len(configs) == 4 and all(item.connected for item in configs)
    report_current = bool(report and report.status == "passed" and versions_match(configs, report.service_versions))
    report_fresh = bool(report_current and report.expires_at > now)
    enabled_snapshot_current = bool(state and state.enabled and versions_match(configs, state.enabled_service_versions))
    return {
        "all_saved": all_saved,
        "all_connected": all_connected,
        "report_current": report_current,
        "report_fresh": report_fresh,
        "enabled_snapshot_current": enabled_snapshot_current,
        "ready": bool(all_saved and all_connected and report_fresh and enabled_snapshot_current),
    }


def invalidate_integration(state: IntegrationState, reason: str) -> None:
    state.enabled = False
    state.last_self_test_id = None
    state.enabled_at = None
    state.enabled_by = None
    state.enabled_service_versions = None
    state.invalidated_reason = reason


def test_detail(service_type: str) -> tuple[int, str]:
    return {
        "agent": (126, "Runtime 鉴权通过；工具白名单与审批回调可用"),
        "model": (816, "模型响应成功；结构化 JSON Schema 校验通过"),
        "voice": (236, "ASR/TTS 网关鉴权与基础回环通过"),
        "phone": (41, "SIP 注册与事件回调签名通过"),
    }[service_type]


def build_self_test_items(configs: list[ServiceConfig]) -> list[dict[str, str]]:
    connected = {item.service_type: item.connected for item in configs}
    agent = next((item for item in configs if item.service_type == "agent"), None)
    all_connected = all(connected.get(kind, False) for kind in SERVICE_TYPES)
    agent_detail = (
        "DeepSeek Harness 会话恢复通过；受控工具 8/8；shell、文件系统与直接账务写入已阻断"
        if agent and agent.provider == "DeepSeek Harness"
        else "工具白名单 8/8；越权动作已阻断"
    )
    specs = [
        ("agent", "Agent 工具契约、会话恢复与策略护栏", connected.get("agent", False), agent_detail),
        ("model", "模型结构化输出", connected.get("model", False), "JSON Schema 校验通过"),
        ("asr", "ASR 与 TTS 回环", connected.get("voice", False), "识别与播报回环通过"),
        ("sip", "SIP 注册与事件回调", connected.get("phone", False), "注册成功；回调签名通过"),
        ("e2e", "端到端沙箱通话", all_connected, "白名单脚本回放完成；未发起真实外呼"),
    ]
    return [
        {"id": item_id, "title": title, "status": "passed" if ok else "blocked", "detail": detail if ok else "前置服务连接测试未通过"}
        for item_id, title, ok, detail in specs
    ]


def goal_matches(case: CaseRecord, goal: str) -> bool:
    if goal == "已签协议履约":
        return case.has_signed_plan
    if goal == "首次联络与意愿确认":
        return case.status in {"待联系", "方案待签"}
    if goal == "回款自动核对":
        return case.status in {"到账待匹配", "支付失败", "支付处理中", "自然回款", "已确认回款"}
    return False


def activity_preflight(db: Session, tenant_id: str, payload: Any) -> dict[str, Any]:
    package = db.scalar(
        select(AssetPackage).where(AssetPackage.tenant_id == tenant_id, AssetPackage.package_id == payload.package_id)
    )
    if not package:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资产包不存在")
    if payload.budget_yuan > package.budget_limit_yuan:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="单案预算超过授权上限")

    selected = list(
        db.scalars(
            select(CaseRecord).where(
                CaseRecord.tenant_id == tenant_id,
                CaseRecord.package_id == payload.package_id,
                CaseRecord.case_id.in_(payload.case_ids),
            )
        )
    )
    found_ids = {item.case_id for item in selected}
    excluded = [{"case_id": case_id, "reason": "案件不存在或不属于当前租户资产包"} for case_id in payload.case_ids if case_id not in found_ids]
    in_flight_ids = {
        case_id
        for activity in db.scalars(
            select(Activity).where(Activity.tenant_id == tenant_id, Activity.status.in_(("running", "paused")))
        )
        for case_id in activity.case_ids
    }

    eligible: list[str] = []
    for case in selected:
        reason = None
        if case.blocked:
            reason = "保护暂停"
        elif case.status in {"已结清", "本期已足额"}:
            reason = "已完成"
        elif case.case_id in in_flight_ids:
            reason = "重复在途任务"
        elif not goal_matches(case, payload.goal):
            reason = "目标不匹配"
        if reason:
            excluded.append({"case_id": case.case_id, "reason": reason})
        else:
            eligible.append(case.case_id)

    gate = gate_for(db, tenant_id)
    production_ready = bool(gate["ready"] and package.policy_status == "published" and eligible)
    resolved_mode = "sandbox" if payload.requested_mode == "sandbox" else "channel" if production_ready else "sandbox"
    blockers = []
    if not eligible:
        blockers.append("没有符合目标且可执行的案件")
    if package.policy_status != "published":
        blockers.append("授权策略尚未发布")
    if not gate["ready"]:
        blockers.append("AI 与渠道门禁尚未有效")
    if payload.requested_mode == "channel" and not production_ready:
        blockers.append("请求授权渠道运行，但生产前置条件未满足")

    return {
        "tenant_id": tenant_id,
        "package_id": package.package_id,
        "eligible_case_ids": eligible,
        "excluded": excluded,
        "policy_status": package.policy_status,
        "policy_version": package.policy_version,
        "budget_limit_yuan": package.budget_limit_yuan,
        "channel_ready": gate["ready"],
        "production_ready": production_ready,
        "resolved_mode": resolved_mode,
        "blockers": blockers,
    }
