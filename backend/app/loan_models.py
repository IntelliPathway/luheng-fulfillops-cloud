"""Isolated standard-loan MVP storage; no new financial ledger."""
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import new_id, utcnow


class LoanContactPolicy(Base):
    __tablename__ = "loan_contact_policies"
    __table_args__ = (
        CheckConstraint("window_start_minute >= 0 AND window_start_minute < window_end_minute AND window_end_minute <= 1440"),
        CheckConstraint("daily_session_limit BETWEEN 1 AND 3"),
        CheckConstraint("snapshot_max_hours BETWEEN 1 AND 24"),
        CheckConstraint("promise_max_days BETWEEN 1 AND 30"),
        CheckConstraint("authorization_minutes BETWEEN 1 AND 30"),
    )
    tenant_id: Mapped[str] = mapped_column(String(40), ForeignKey("tenants.id"), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Shanghai")
    window_start_minute: Mapped[int] = mapped_column(Integer)
    window_end_minute: Mapped[int] = mapped_column(Integer)
    daily_session_limit: Mapped[int] = mapped_column(Integer)
    snapshot_max_hours: Mapped[int] = mapped_column(Integer)
    promise_max_days: Mapped[int] = mapped_column(Integer)
    authorization_minutes: Mapped[int] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    authority_reference: Mapped[str] = mapped_column(String(160))
    valid_until: Mapped[datetime] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoanProfile(Base):
    __tablename__ = "loan_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.case_id"]),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("LOAN"))
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    case_id: Mapped[str] = mapped_column(String(40))
    product: Mapped[str] = mapped_column(String(80))
    due_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer)
    contact_reference: Mapped[str] = mapped_column(String(160))
    source_reference: Mapped[str] = mapped_column(String(160))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime)
    ledger_baseline: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(80))


class LoanSession(Base):
    __tablename__ = "loan_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_key"),
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "case_id"], ["cases.tenant_id", "cases.case_id"]),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("LC"))
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    case_id: Mapped[str] = mapped_column(String(40))
    request_key: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(24), default="sandbox")
    state: Mapped[str] = mapped_column(String(24), default="identity_pending")
    profile_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    authorized_by: Mapped[str] = mapped_column(String(80))
    authorization_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    promise: Mapped[dict] = mapped_column(JSON, default=dict)


class LoanEvent(Base):
    __tablename__ = "loan_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id", "event_key"),
        ForeignKeyConstraint(["tenant_id", "session_id"], ["loan_sessions.tenant_id", "loan_sessions.id"]),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("LE"))
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    event_key: Mapped[str] = mapped_column(String(80))
    payload_digest: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
