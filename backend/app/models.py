from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .version import APP_VERSION, PRODUCT_NAME


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
    __table_args__ = (
        UniqueConstraint("tenant_id", "package_id"),
        CheckConstraint("policy_status IN ('draft','published')", name="ck_asset_packages_policy_status"),
        CheckConstraint("budget_limit_yuan > 0", name="ck_asset_packages_budget_positive"),
        CheckConstraint("min_settlement_bps BETWEEN 1000 AND 10000", name="ck_asset_packages_settlement_bps"),
        CheckConstraint("max_installments BETWEEN 1 AND 60", name="ck_asset_packages_installments"),
        CheckConstraint("min_down_payment_bps BETWEEN 0 AND 10000", name="ck_asset_packages_down_payment_bps"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PK"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(120))
    policy_status: Mapped[str] = mapped_column(String(24), default="published")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    budget_limit_yuan: Mapped[float] = mapped_column(Float, default=30)
    min_settlement_bps: Mapped[int] = mapped_column(Integer, default=7000)
    max_installments: Mapped[int] = mapped_column(Integer, default=6)
    min_down_payment_bps: Mapped[int] = mapped_column(Integer, default=2000)
    source_import_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PolicyProposal(Base):
    __tablename__ = "policy_proposals"
    __table_args__ = (
        Index("ix_policy_proposals_tenant_package", "tenant_id", "package_id", "created_at"),
        Index("ix_policy_proposals_tenant_status", "tenant_id", "status", "created_at"),
        ForeignKeyConstraint(["tenant_id", "package_id"], ["asset_packages.tenant_id", "asset_packages.package_id"]),
        CheckConstraint("status IN ('pending_review','approved','rejected')", name="ck_policy_proposals_status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("POL"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    expected_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    proposal_reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CaseRecord(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id"),
        ForeignKeyConstraint(["tenant_id", "package_id"], ["asset_packages.tenant_id", "asset_packages.package_id"]),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CASE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    has_signed_plan: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_basis_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_import_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AssetImportBatch(Base):
    __tablename__ = "asset_import_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index("ix_asset_import_batches_tenant_created", "tenant_id", "created_at"),
        Index("ix_asset_import_batches_tenant_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("IMP"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(160), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(24), default="asset-case-v2", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    package_count: Mapped[int] = mapped_column(Integer, default=0)
    total_claim_balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    normalized_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    committed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class TelephonyEvent(Base):
    __tablename__ = "telephony_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_event_id"),
        Index("ix_telephony_events_tenant_call", "tenant_id", "call_reference", "occurred_at"),
        Index("ix_telephony_events_tenant_status", "tenant_id", "status", "occurred_at"),
        CheckConstraint("event_type IN ('initiated','ringing','answered','completed','failed')", name="ck_telephony_event_type"),
        CheckConstraint("status IN ('initiated','ringing','answered','completed','failed')", name="ck_telephony_status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("TEL"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    provider_event_id: Mapped[str] = mapped_column(String(120), index=True)
    call_reference: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    activity_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


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
    __table_args__ = (
        UniqueConstraint("tenant_id", "activity_id"),
        ForeignKeyConstraint(["tenant_id", "package_id"], ["asset_packages.tenant_id", "asset_packages.package_id"]),
    )

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
    version: Mapped[str] = mapped_column(String(24), default=APP_VERSION)
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
    title: Mapped[str] = mapped_column(String(160), default=f"{PRODUCT_NAME} 会话")
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


class ModelReplayRun(Base):
    __tablename__ = "model_replay_runs"
    __table_args__ = (
        Index("ix_model_replay_runs_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("job_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("REPLAY"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("async_jobs.id"), nullable=False, unique=True, index=True)
    suite_name: Mapped[str] = mapped_column(String(120), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="deterministic-contract")
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    profile: Mapped[str] = mapped_column(String(120), nullable=False)
    model_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    dataset_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    external_call_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


class CommissionRule(Base):
    __tablename__ = "commission_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rule_id", "version"),
        ForeignKeyConstraint(["tenant_id", "package_id"], ["asset_packages.tenant_id", "asset_packages.package_id"]),
        CheckConstraint("rate_bps BETWEEN 0 AND 10000", name="ck_commission_rules_rate_bps"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CRULE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    rate_bps: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class CaseFinancialProfile(Base):
    __tablename__ = "case_financial_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.case_id"]),
        CheckConstraint("claim_balance_cents > 0", name="ck_case_financial_profiles_claim_positive"),
        CheckConstraint("principal_cents IS NULL OR principal_cents >= 0", name="ck_case_financial_profiles_principal"),
        CheckConstraint("interest_cents IS NULL OR interest_cents >= 0", name="ck_case_financial_profiles_interest"),
        CheckConstraint("fee_cents IS NULL OR fee_cents >= 0", name="ck_case_financial_profiles_fee"),
        CheckConstraint("mandate_start <= mandate_end", name="ck_case_financial_profiles_mandate_dates"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CFP"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    commission_rule_id: Mapped[str] = mapped_column(String(80), index=True)
    claim_balance_cents: Mapped[int] = mapped_column(Integer, default=1)
    principal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interest_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_overdue_date: Mapped[date | None] = mapped_column(nullable=True)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mandate_start: Mapped[date] = mapped_column(nullable=False)
    mandate_end: Mapped[date] = mapped_column(nullable=False)
    signed_plan_at: Mapped[date | None] = mapped_column(nullable=True)
    signed_plan_last_due: Mapped[date | None] = mapped_column(nullable=True)
    signed_plan_tail_eligible: Mapped[bool] = mapped_column(Boolean, default=False)


class PaymentWebhookConfig(Base):
    __tablename__ = "payment_webhook_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PWH"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(12), index=True)
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_last4: Mapped[str] = mapped_column(String(8), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_amount_cents: Mapped[int] = mapped_column(Integer, default=100_000_000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_event_id"),
        Index("ix_payment_receipts_tenant_status", "tenant_id", "status", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PRC"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(12), index=True)
    provider_event_id: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(24))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    original_provider_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_version: Mapped[str] = mapped_column(String(12), default="v1")
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="accepted", index=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recovery_entry_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PaymentReconciliation(Base):
    __tablename__ = "payment_reconciliations"
    __table_args__ = (
        UniqueConstraint("receipt_id"),
        Index("ix_payment_reconciliations_tenant_status", "tenant_id", "status", "proposed_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("REC"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("payment_receipts.id"), unique=True, index=True)
    proposed_case_id: Mapped[str] = mapped_column(String(40), index=True)
    candidate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    proposed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_entry_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ProtectionIncident(Base):
    __tablename__ = "protection_incidents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_event_id"),
        Index("ix_protection_incidents_tenant_status", "tenant_id", "status", "opened_at"),
        Index("ix_protection_incidents_tenant_case", "tenant_id", "case_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PROT"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    source_event_id: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    priority: Mapped[str] = mapped_column(String(4), default="P1", index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    opening_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    release_policy: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_case_status: Mapped[str] = mapped_column(String(40), nullable=False)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_by: Mapped[str] = mapped_column(String(80), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    case_released: Mapped[bool] = mapped_column(Boolean, default=False)


class RepaymentPlan(Base):
    __tablename__ = "repayment_plans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "plan_id"),
        Index("ix_repayment_plans_tenant_case_status", "tenant_id", "case_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RPLAN"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    claim_balance_cents: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)
    down_payment_cents: Mapped[int] = mapped_column(Integer)
    installment_count: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    agreement_reference: Mapped[str] = mapped_column(String(120))
    agreement_digest: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_reason: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class RepaymentInstallment(Base):
    __tablename__ = "repayment_installments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "installment_id"),
        UniqueConstraint("plan_row_id", "installment_no"),
        Index("ix_repayment_installments_tenant_due", "tenant_id", "due_date", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RINS"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_row_id: Mapped[str] = mapped_column(ForeignKey("repayment_plans.id"), index=True)
    installment_id: Mapped[str] = mapped_column(String(120), index=True)
    installment_no: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(nullable=False, index=True)
    due_cents: Mapped[int] = mapped_column(Integer)
    paid_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class RepaymentAllocation(Base):
    __tablename__ = "repayment_allocations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "allocation_id"),
        UniqueConstraint("tenant_id", "recovery_entry_id", "installment_row_id"),
        Index("ix_repayment_allocations_tenant_recovery", "tenant_id", "recovery_entry_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RALL"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    allocation_id: Mapped[str] = mapped_column(String(120), index=True)
    recovery_entry_id: Mapped[str] = mapped_column(String(120), index=True)
    plan_row_id: Mapped[str] = mapped_column(ForeignKey("repayment_plans.id"), index=True)
    installment_row_id: Mapped[str] = mapped_column(ForeignKey("repayment_installments.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    source_allocation_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RecoveryLedgerEntry(Base):
    __tablename__ = "recovery_ledger_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entry_id"),
        UniqueConstraint("receipt_id"),
        Index("ix_recovery_ledger_tenant_booked", "tenant_id", "booked_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RLE"))
    entry_id: Mapped[str] = mapped_column(String(120), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    receipt_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    package_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(24))
    amount_cents: Mapped[int] = mapped_column(Integer)
    eligible_amount_cents: Mapped[int] = mapped_column(Integer)
    commission_rule_id: Mapped[str] = mapped_column(String(80))
    commission_rule_version: Mapped[int] = mapped_column(Integer)
    rate_bps: Mapped[int] = mapped_column(Integer)
    commission_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))
    original_entry_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    allocation: Mapped[str] = mapped_column(String(160), default="按案件直接匹配")
    source: Mapped[str] = mapped_column(String(120))
    booked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class CommissionLedgerEntry(Base):
    __tablename__ = "commission_ledger_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        UniqueConstraint("tenant_id", "source_recovery_entry_id"),
        Index("ix_commission_ledger_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CLE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(24), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    source_recovery_entry_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reference: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
