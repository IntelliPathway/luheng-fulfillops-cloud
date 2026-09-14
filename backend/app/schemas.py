from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ServiceType = Literal["agent", "model", "voice", "phone"]
ActivityGoal = Literal["已签协议履约", "首次联络与意愿确认", "回款自动核对"]


class ServiceConfigUpsert(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    settings: dict[str, Any]
    credential: str | None = Field(default=None, min_length=4, max_length=4096)


class ServiceConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_type: str
    provider: str
    settings: dict[str, Any]
    credential_mask: str | None
    version: int
    saved: bool
    connected: bool
    latency_ms: int | None
    connection_tested_at: datetime | None
    updated_at: datetime


class ConnectionTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    service_type: str
    config_version: int
    status: str
    latency_ms: int | None
    detail: str
    created_at: datetime


class SelfTestItem(BaseModel):
    id: str
    title: str
    status: str
    detail: str


class SelfTestReportOut(BaseModel):
    id: str
    status: str
    service_versions: dict[str, int]
    items: list[SelfTestItem]
    created_at: datetime
    expires_at: datetime


class GateOut(BaseModel):
    all_saved: bool
    all_connected: bool
    report_current: bool
    report_fresh: bool
    enabled_snapshot_current: bool
    ready: bool


class IntegrationStateOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    enabled_by: str | None
    enabled_service_versions: dict[str, int] | None
    invalidated_reason: str | None


class IntegrationOverviewOut(BaseModel):
    tenant_id: str
    services: dict[str, ServiceConfigOut]
    state: IntegrationStateOut
    gate: GateOut
    last_report: SelfTestReportOut | None


class EnableRequest(BaseModel):
    acknowledged: bool


class ActivityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    package_id: str = Field(min_length=1, max_length=40)
    goal: ActivityGoal
    budget_yuan: float = Field(gt=0, le=100000)
    case_ids: list[str] = Field(min_length=1, max_length=1000)
    requested_mode: Literal["auto", "sandbox", "channel"] = "auto"


class ExcludedCase(BaseModel):
    case_id: str
    reason: str


class ActivityPreflightOut(BaseModel):
    tenant_id: str
    package_id: str
    eligible_case_ids: list[str]
    excluded: list[ExcludedCase]
    policy_status: str
    policy_version: int
    budget_limit_yuan: float
    channel_ready: bool
    production_ready: bool
    resolved_mode: Literal["sandbox", "channel"]
    blockers: list[str]


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activity_id: str
    name: str
    package_id: str
    goal: str
    status: str
    mode: str
    budget_yuan: float
    case_ids: list[str]
    policy_version: int
    service_snapshot: dict[str, Any]
    preflight: dict[str, Any]
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    service: str
    version: str


class AuthSessionOut(BaseModel):
    actor_id: str
    display_name: str
    email: str
    tenant_id: str
    role: str
    auth_mode: str


class DevTokenRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=80)


class DevTokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 3600


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    attempt: int
    max_attempts: int
    idempotency_key: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AsyncTestRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=120)


class AgentGatewayOut(BaseModel):
    mode: str
    provider: str
    profile: str
    connected: bool
    transport: str
    session_persistence: str
    supported_runtimes: list[str]
    tools: list[dict[str, Any]]
    forbidden_tools: list[str]
    approval_policy: str
    safety_boundary: str


class AgentRuntimeOut(BaseModel):
    session_id: str
    provider: str
    profile: str
    provider_session_id: str | None
    event_cursor: int
    turn_count: int
    last_run_id: str | None
    runtime_metadata: dict[str, Any]
    updated_at: datetime | None


class AgentToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentSessionCreate(BaseModel):
    scope_type: Literal["global", "activity", "case"] = "global"
    scope_id: str | None = Field(default=None, max_length=80)
    title: str = Field(default="履衡 AI 会话", min_length=1, max_length=160)


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope_type: str
    scope_id: str | None
    runtime_provider: str
    runtime_profile: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=120)


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_type: str
    arguments: dict[str, Any]
    status: str
    required_role: str
    expires_at: datetime
    decided_at: datetime | None
    decided_by: str | None


class ProposalDecisionRequest(BaseModel):
    acknowledged: bool
