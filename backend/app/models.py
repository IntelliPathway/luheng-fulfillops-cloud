from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12].upper()}"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("MEM"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AssetPackage(Base):
    __tablename__ = "asset_packages"
    __table_args__ = (UniqueConstraint("tenant_id", "package_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PK"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(120))
    policy_status: Mapped[str] = mapped_column(String(24), default="published")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    budget_limit_yuan: Mapped[float] = mapped_column(Float, default=30)


class CaseRecord(Base):
    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CASE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    has_signed_plan: Mapped[bool] = mapped_column(Boolean, default=False)


class ServiceConfig(Base):
    __tablename__ = "service_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "service_type"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CFG"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_type: Mapped[str] = mapped_column(String(24), index=True)
    provider: Mapped[str] = mapped_column(String(120))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    saved: Mapped[bool] = mapped_column(Boolean, default=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ManagedSecret(Base):
    __tablename__ = "managed_secrets"
    __table_args__ = (
        UniqueConstraint("reference"),
        Index("ix_managed_secrets_tenant_service", "tenant_id", "service_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("SEC"))
    reference: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_type: Mapped[str] = mapped_column(String(24), index=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(24), default="A256GCM")
    key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IntegrationState(Base):
    __tablename__ = "integration_states"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_self_test_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    enabled_service_versions: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConnectionTest(Base):
    __tablename__ = "connection_tests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CT"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    service_type: Mapped[str] = mapped_column(String(24), index=True)
    config_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SelfTestReport(Base):
    __tablename__ = "self_test_reports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("SELF"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    service_versions: Mapped[dict[str, int]] = mapped_column(JSON)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("tenant_id", "activity_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ACTROW"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    goal: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="running")
    mode: Mapped[str] = mapped_column(String(24))
    budget_yuan: Mapped[float] = mapped_column(Float)
    case_ids: Mapped[list[str]] = mapped_column(JSON)
    policy_version: Mapped[int] = mapped_column(Integer)
    service_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    preflight: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AUD"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AsyncJob(Base):
    __tablename__ = "async_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "idempotency_key"),
        Index("ix_async_jobs_queue_claim", "queue_name", "status", "available_at", "priority", "created_at"),
        Index("ix_async_jobs_lease_expiry", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("JOB"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    queue_name: Mapped[str] = mapped_column(String(40), default="default", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobWorker(Base):
    __tablename__ = "job_workers"
    __table_args__ = (Index("ix_job_workers_heartbeat", "status", "heartbeat_at"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="starting", index=True)
    queues: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    version: Mapped[str] = mapped_column(String(24), default="0.6.0")
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ASES"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(80), index=True)
    scope_type: Mapped[str] = mapped_column(String(24), default="global")
    scope_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    runtime_provider: Mapped[str] = mapped_column(String(120))
    runtime_profile: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160), default="履衡 AI 会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("MSG"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    structured: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RUN"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("async_jobs.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(120))
    profile: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24))
    tool_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentRuntimeCheckpoint(Base):
    __tablename__ = "agent_runtime_checkpoints"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ACK"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    provider_session_id: Mapped[str] = mapped_column(String(120), index=True)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    runtime_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PROP"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("agent_messages.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    required_role: Mapped[str] = mapped_column(String(24), default="operator")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class BusinessMetricSnapshot(Base):
    __tablename__ = "business_metric_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "metric_key"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("MET"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    metric_key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(24), default="yuan")
    source: Mapped[str] = mapped_column(String(120))
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
