from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain import utcnow
from .models import (
    AuditEvent,
    BusinessMetricSnapshot,
    CaseFinancialProfile,
    CaseRecord,
    CommissionLedgerEntry,
    CommissionRule,
    PaymentReceipt,
    PaymentWebhookConfig,
    RecoveryLedgerEntry,
)
from .secret_store import SecretStoreError, resolve_secret, store_secret

PAYMENT_SIGNATURE_VERSION = "v1"
PAYMENT_CURRENCY = "CNY"
SUPPORTED_PAYMENT_EVENTS = {"payment", "refund"}
SUPPORTED_COMMISSION_EVENTS = {"settlement", "collection"}
PROVIDER_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,11}")


class FinancialLedgerError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class AcceptedReceipt:
    receipt: PaymentReceipt
    duplicate: bool


def payment_sandbox_enabled() -> bool:
    return os.getenv("ENABLE_PAYMENT_SANDBOX", "false").strip().lower() in {"1", "true", "yes", "on"}


def validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if not PROVIDER_PATTERN.fullmatch(normalized):
        raise FinancialLedgerError("invalid_provider", "支付 Provider 标识格式无效")
    return normalized


def payment_secret_scope(provider: str) -> str:
    return f"payment-{validate_provider(provider)}"


def _bounded_tolerance() -> int:
    try:
        value = int(os.getenv("PAYMENT_WEBHOOK_TOLERANCE_SECONDS", "300"))
    except ValueError as exc:
        raise FinancialLedgerError("invalid_deployment_policy", "支付回执时间窗配置无效", 503) from exc
    if not 60 <= value <= 900:
        raise FinancialLedgerError("invalid_deployment_policy", "支付回执时间窗必须在 60 到 900 秒之间", 503)
    return value


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def configure_payment_webhook(
    db: Session,
    tenant_id: str,
    provider: str,
    credential: str | None,
    max_amount_cents: int,
) -> PaymentWebhookConfig:
    provider = validate_provider(provider)
    if not 1 <= max_amount_cents <= 1_000_000_000:
        raise FinancialLedgerError("invalid_amount_limit", "单笔回执金额上限必须在 1 分到 1,000 万元之间")
    config = db.scalar(
        select(PaymentWebhookConfig).where(
            PaymentWebhookConfig.tenant_id == tenant_id,
            PaymentWebhookConfig.provider == provider,
        )
    )
    if not config:
        if not credential:
            raise FinancialLedgerError("credential_required", "首次配置支付回执必须提供签名密钥")
        config = PaymentWebhookConfig(
            tenant_id=tenant_id,
            provider=provider,
            secret_ref="pending",
            credential_last4="",
            version=0,
        )
        db.add(config)
        db.flush()
    if credential:
        try:
            config.secret_ref, config.credential_last4 = store_secret(
                db,
                tenant_id,
                payment_secret_scope(provider),
                credential,
                previous_reference=config.secret_ref if config.secret_ref != "pending" else None,
            )
        except SecretStoreError as exc:
            raise FinancialLedgerError("secret_store_unavailable", str(exc), 503) from exc
    if config.secret_ref == "pending":
        raise FinancialLedgerError("credential_required", "支付回执签名密钥不可用")
    config.version += 1
    config.active = True
    config.max_amount_cents = max_amount_cents
    config.updated_at = utcnow()
    return config


def _resolve_webhook_secret(db: Session, config: PaymentWebhookConfig) -> str:
    try:
        credential = resolve_secret(
            db,
            config.secret_ref,
            config.tenant_id,
            payment_secret_scope(config.provider),
        )
    except SecretStoreError as exc:
        raise FinancialLedgerError("webhook_secret_unavailable", "支付回执签名密钥暂时不可用", 503) from exc
    if not credential:
        raise FinancialLedgerError("webhook_secret_unavailable", "支付回执签名密钥不可解析", 503)
    return credential


def sign_payment_webhook(secret: str, timestamp: str, raw_body: bytes) -> str:
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return f"{PAYMENT_SIGNATURE_VERSION}={signature}"


def verify_payment_webhook(
    db: Session,
    tenant_id: str,
    provider: str,
    raw_body: bytes,
    timestamp: str,
    signature: str,
) -> tuple[PaymentWebhookConfig, str, str]:
    provider = validate_provider(provider)
    config = db.scalar(
        select(PaymentWebhookConfig).where(
            PaymentWebhookConfig.tenant_id == tenant_id,
            PaymentWebhookConfig.provider == provider,
            PaymentWebhookConfig.active.is_(True),
        )
    )
    if not config:
        raise FinancialLedgerError("webhook_not_configured", "支付回执 Provider 未配置", 404)
    try:
        signed_at = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise FinancialLedgerError("invalid_signature_timestamp", "支付回执签名时间无效", 401) from exc
    now = int(datetime.now(UTC).timestamp())
    if abs(now - signed_at) > _bounded_tolerance():
        raise FinancialLedgerError("signature_expired", "支付回执签名已超出允许时间窗", 401)
    if not re.fullmatch(r"v1=[0-9a-f]{64}", signature or ""):
        raise FinancialLedgerError("invalid_signature", "支付回执签名格式无效", 401)
    secret = _resolve_webhook_secret(db, config)
    expected = sign_payment_webhook(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected, signature):
        raise FinancialLedgerError("invalid_signature", "支付回执签名校验失败", 401)
    return config, hashlib.sha256(raw_body).hexdigest(), hashlib.sha256(signature.encode()).hexdigest()


def _commission_cents(amount_cents: int, rate_bps: int) -> int:
    sign = -1 if amount_cents < 0 else 1
    return sign * ((abs(amount_cents) * rate_bps + 5_000) // 10_000)


def _financial_profile(db: Session, tenant_id: str, case_id: str) -> tuple[CaseRecord, CaseFinancialProfile, CommissionRule]:
    case = db.scalar(
        select(CaseRecord).where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id)
    )
    profile = db.scalar(
        select(CaseFinancialProfile).where(
            CaseFinancialProfile.tenant_id == tenant_id,
            CaseFinancialProfile.case_id == case_id,
        )
    )
    if not case or not profile:
        raise FinancialLedgerError("case_unmatched", "回执未匹配到当前租户的完整案件财务档案")
    rule = db.scalar(
        select(CommissionRule).where(
            CommissionRule.tenant_id == tenant_id,
            CommissionRule.rule_id == profile.commission_rule_id,
            CommissionRule.package_id == case.package_id,
            CommissionRule.status == "active",
        ).order_by(CommissionRule.version.desc())
    )
    if not rule:
        raise FinancialLedgerError("commission_rule_missing", "案件缺少有效佣金规则")
    return case, profile, rule


def _payment_eligibility(profile: CaseFinancialProfile, occurred_at: datetime) -> tuple[bool, str]:
    booked = occurred_at.date()
    if booked < profile.mandate_start:
        return False, "PRE_MANDATE"
    if booked <= profile.mandate_end:
        return True, "IN_MANDATE"
    tail_valid = (
        profile.signed_plan_tail_eligible
        and profile.signed_plan_at is not None
        and profile.signed_plan_at <= profile.mandate_end
        and profile.signed_plan_last_due is not None
        and booked <= profile.signed_plan_last_due
    )
    return (True, "SIGNED_PLAN_TAIL") if tail_valid else (False, "OUTSIDE_TAIL")


def _add_commission_entry(
    db: Session,
    tenant_id: str,
    recovery: RecoveryLedgerEntry,
    created_by: str,
) -> CommissionLedgerEntry:
    event_type = "accrual" if recovery.commission_cents >= 0 else "reversal"
    idempotency_key = f"recovery:{hashlib.sha256(recovery.entry_id.encode()).hexdigest()[:48]}"
    payload_digest = _canonical_digest(
        {
            "event_type": event_type,
            "amount_cents": recovery.commission_cents,
            "source_recovery_entry_id": recovery.entry_id,
        }
    )
    entry = CommissionLedgerEntry(
        tenant_id=tenant_id,
        event_id=f"COM-{uuid4().hex[:20].upper()}",
        event_type=event_type,
        amount_cents=recovery.commission_cents,
        source_recovery_entry_id=recovery.entry_id,
        reference=f"回款账簿 {recovery.entry_id}",
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        created_by=created_by,
        occurred_at=recovery.booked_at,
    )
    db.add(entry)
    return entry


def _reconcile_receipt(db: Session, receipt: PaymentReceipt, created_by: str) -> RecoveryLedgerEntry:
    if receipt.recovery_entry_id:
        existing = db.scalar(
            select(RecoveryLedgerEntry).where(
                RecoveryLedgerEntry.tenant_id == receipt.tenant_id,
                RecoveryLedgerEntry.entry_id == receipt.recovery_entry_id,
            )
        )
        if existing:
            return existing
    case_id = str(receipt.case_id or "").strip().upper()
    if not case_id:
        raise FinancialLedgerError("case_unmatched", "回执缺少可匹配的案件编号")
    if receipt.event_type == "refund":
        if not receipt.original_provider_event_id:
            raise FinancialLedgerError("original_payment_required", "退款回执必须引用原支付事件")
        original_receipt = db.scalar(
            select(PaymentReceipt).where(
                PaymentReceipt.tenant_id == receipt.tenant_id,
                PaymentReceipt.provider == receipt.provider,
                PaymentReceipt.provider_event_id == receipt.original_provider_event_id,
                PaymentReceipt.status == "matched",
            )
        )
        if not original_receipt or not original_receipt.recovery_entry_id:
            raise FinancialLedgerError("original_payment_missing", "退款引用的原支付事件不存在或尚未匹配")
        original = db.scalar(
            select(RecoveryLedgerEntry).where(
                RecoveryLedgerEntry.tenant_id == receipt.tenant_id,
                RecoveryLedgerEntry.entry_id == original_receipt.recovery_entry_id,
                RecoveryLedgerEntry.event_type == "PAYMENT",
            ).with_for_update()
        )
        if not original or original.case_id != case_id:
            raise FinancialLedgerError("refund_case_mismatch", "退款案件与原支付事件不一致")
        prior_refunds = list(
            db.scalars(
                select(RecoveryLedgerEntry).where(
                    RecoveryLedgerEntry.tenant_id == receipt.tenant_id,
                    RecoveryLedgerEntry.original_entry_id == original.entry_id,
                )
            )
        )
        if sum(abs(row.amount_cents) for row in prior_refunds) + receipt.amount_cents > original.amount_cents:
            raise FinancialLedgerError("refund_exceeds_payment", "累计退款金额不能超过原支付金额")
        amount_cents = -receipt.amount_cents
        eligible_cents = amount_cents if original.eligible_amount_cents else 0
        rule_id = original.commission_rule_id
        rule_version = original.commission_rule_version
        rate_bps = original.rate_bps
        reason = "REFUND_ORIGINAL_RATE"
        package_id = original.package_id
        original_entry_id = original.entry_id
    else:
        case, profile, rule = _financial_profile(db, receipt.tenant_id, case_id)
        eligible, reason = _payment_eligibility(profile, receipt.occurred_at)
        amount_cents = receipt.amount_cents
        eligible_cents = amount_cents if eligible else 0
        rule_id = rule.rule_id
        rule_version = rule.version
        rate_bps = rule.rate_bps
        package_id = case.package_id
        original_entry_id = None
    entry = RecoveryLedgerEntry(
        entry_id=f"{receipt.provider}:{receipt.provider_event_id}",
        tenant_id=receipt.tenant_id,
        receipt_id=receipt.id,
        case_id=case_id,
        package_id=package_id,
        event_type=receipt.event_type.upper(),
        amount_cents=amount_cents,
        eligible_amount_cents=eligible_cents,
        commission_rule_id=rule_id,
        commission_rule_version=rule_version,
        rate_bps=rate_bps,
        commission_cents=_commission_cents(eligible_cents, rate_bps),
        reason=reason,
        original_entry_id=original_entry_id,
        allocation=f"{case_id} · 按案件引用匹配",
        source=f"{receipt.provider} 验签回执",
        booked_at=receipt.occurred_at,
    )
    db.add(entry)
    db.flush()
    _add_commission_entry(db, receipt.tenant_id, entry, created_by)
    receipt.status = "matched"
    receipt.failure_code = None
    receipt.recovery_entry_id = entry.entry_id
    receipt.updated_at = utcnow()
    return entry


def accept_payment_webhook(
    db: Session,
    tenant_id: str,
    provider: str,
    payload: Any,
    raw_body: bytes,
    timestamp: str,
    signature: str,
) -> AcceptedReceipt:
    config, payload_digest, signature_digest = verify_payment_webhook(
        db, tenant_id, provider, raw_body, timestamp, signature
    )
    event_id = str(payload.event_id)
    existing = db.scalar(
        select(PaymentReceipt).where(
            PaymentReceipt.tenant_id == tenant_id,
            PaymentReceipt.provider == config.provider,
            PaymentReceipt.provider_event_id == event_id,
        )
    )
    if existing:
        if existing.payload_digest != payload_digest:
            raise FinancialLedgerError("idempotency_conflict", "相同支付事件编号对应了不同载荷", 409)
        existing.duplicate_count += 1
        existing.updated_at = utcnow()
        db.commit()
        return AcceptedReceipt(existing, True)
    if payload.currency != PAYMENT_CURRENCY:
        raise FinancialLedgerError("currency_denied", "当前账簿仅接受 CNY 回执")
    if payload.amount_cents > config.max_amount_cents:
        raise FinancialLedgerError("amount_limit_exceeded", "支付回执金额超过租户配置上限")
    occurred_at = _naive_utc(payload.occurred_at)
    if occurred_at > utcnow() + timedelta(seconds=_bounded_tolerance()):
        raise FinancialLedgerError("occurred_at_in_future", "支付回执发生时间超出允许的未来时间窗")
    receipt = PaymentReceipt(
        tenant_id=tenant_id,
        provider=config.provider,
        provider_event_id=event_id,
        event_type=payload.event_type,
        amount_cents=payload.amount_cents,
        currency=payload.currency,
        occurred_at=occurred_at,
        case_id=payload.case_id.upper() if payload.case_id else None,
        original_provider_event_id=payload.original_event_id,
        payload_digest=payload_digest,
        signature_digest=signature_digest,
        signature_version=PAYMENT_SIGNATURE_VERSION,
        signature_verified=True,
        status="accepted",
    )
    db.add(receipt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(
            select(PaymentReceipt).where(
                PaymentReceipt.tenant_id == tenant_id,
                PaymentReceipt.provider == config.provider,
                PaymentReceipt.provider_event_id == event_id,
            )
        )
        if raced and raced.payload_digest == payload_digest:
            raced.duplicate_count += 1
            db.commit()
            return AcceptedReceipt(raced, True)
        raise FinancialLedgerError("idempotency_conflict", "支付事件并发写入冲突", 409) from None
    try:
        _reconcile_receipt(db, receipt, f"webhook:{config.provider}")
    except FinancialLedgerError as exc:
        receipt.status = "unmatched" if exc.code == "case_unmatched" else "review_required"
        receipt.failure_code = exc.code
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=f"webhook:{config.provider}",
            action="payment.receipt.accepted",
            resource_type="payment_receipt",
            resource_id=receipt.id,
            detail={
                "provider": config.provider,
                "provider_event_id": receipt.provider_event_id,
                "status": receipt.status,
                "payload_digest": payload_digest,
            },
        )
    )
    refresh_business_metrics(db, tenant_id)
    db.commit()
    db.refresh(receipt)
    return AcceptedReceipt(receipt, False)


def match_payment_receipt(
    db: Session,
    receipt: PaymentReceipt,
    case_id: str,
    actor_id: str,
) -> RecoveryLedgerEntry:
    if receipt.status == "matched":
        entry = db.scalar(
            select(RecoveryLedgerEntry).where(
                RecoveryLedgerEntry.tenant_id == receipt.tenant_id,
                RecoveryLedgerEntry.entry_id == receipt.recovery_entry_id,
            )
        )
        if not entry:
            raise FinancialLedgerError("ledger_integrity_error", "已匹配回执缺少对应回款账簿", 409)
        return entry
    receipt.case_id = case_id.strip().upper()
    entry = _reconcile_receipt(db, receipt, actor_id)
    db.add(
        AuditEvent(
            tenant_id=receipt.tenant_id,
            actor_id=actor_id,
            action="payment.receipt.matched",
            resource_type="payment_receipt",
            resource_id=receipt.id,
            detail={"case_id": entry.case_id, "recovery_entry_id": entry.entry_id},
        )
    )
    refresh_business_metrics(db, receipt.tenant_id)
    db.commit()
    db.refresh(entry)
    return entry


def financial_summary(db: Session, tenant_id: str) -> dict[str, int]:
    recoveries = list(db.scalars(select(RecoveryLedgerEntry).where(RecoveryLedgerEntry.tenant_id == tenant_id)))
    commissions = list(db.scalars(select(CommissionLedgerEntry).where(CommissionLedgerEntry.tenant_id == tenant_id)))
    pending = list(
        db.scalars(
            select(PaymentReceipt).where(
                PaymentReceipt.tenant_id == tenant_id,
                PaymentReceipt.status.in_({"unmatched", "review_required"}),
            )
        )
    )
    accrued = sum(row.amount_cents for row in commissions if row.event_type in {"accrual", "reversal"})
    settled = sum(row.amount_cents for row in commissions if row.event_type == "settlement")
    collected = sum(row.amount_cents for row in commissions if row.event_type == "collection")
    return {
        "confirmed_net_recovery_cents": sum(row.amount_cents for row in recoveries),
        "commission_eligible_recovery_cents": sum(row.eligible_amount_cents for row in recoveries),
        "accrued_commission_cents": accrued,
        "settled_commission_cents": settled,
        "collected_commission_cents": collected,
        "unsettled_commission_cents": accrued - settled,
        "uncollected_settlement_cents": settled - collected,
        "pending_receipt_count": len(pending),
    }


def refresh_business_metrics(db: Session, tenant_id: str) -> dict[str, int]:
    summary = financial_summary(db, tenant_id)
    values = {
        "confirmed_net_recovery": summary["confirmed_net_recovery_cents"] / 100,
        "commission_eligible_recovery": summary["commission_eligible_recovery_cents"] / 100,
        "accrued_commission": summary["accrued_commission_cents"] / 100,
        "settled_commission": summary["settled_commission_cents"] / 100,
        "collected_commission": summary["collected_commission_cents"] / 100,
    }
    now = utcnow()
    for key, value in values.items():
        metric = db.scalar(
            select(BusinessMetricSnapshot).where(
                BusinessMetricSnapshot.tenant_id == tenant_id,
                BusinessMetricSnapshot.metric_key == key,
            )
        )
        if not metric:
            metric = BusinessMetricSnapshot(tenant_id=tenant_id, metric_key=key, value=value, source="不可变回款与佣金账簿")
            db.add(metric)
        metric.value = value
        metric.source = "不可变回款与佣金账簿"
        metric.as_of = now
    return summary


def record_commission_event(
    db: Session,
    tenant_id: str,
    event_type: str,
    amount_cents: int,
    reference: str,
    idempotency_key: str,
    actor_id: str,
    occurred_at: datetime,
) -> tuple[CommissionLedgerEntry, bool]:
    if event_type not in SUPPORTED_COMMISSION_EVENTS:
        raise FinancialLedgerError("invalid_commission_event", "只允许登记结算或实收佣金事件")
    occurred_at = _naive_utc(occurred_at)
    digest = _canonical_digest(
        {"event_type": event_type, "amount_cents": amount_cents, "reference": reference, "occurred_at": occurred_at.isoformat()}
    )
    # PostgreSQL serializes balance checks for a tenant by locking its existing
    # commission history. SQLite ignores FOR UPDATE and remains the local-only path.
    list(
        db.scalars(
            select(CommissionLedgerEntry.id)
            .where(CommissionLedgerEntry.tenant_id == tenant_id)
            .with_for_update()
        )
    )
    existing = db.scalar(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.tenant_id == tenant_id,
            CommissionLedgerEntry.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.payload_digest != digest:
            raise FinancialLedgerError("idempotency_conflict", "相同幂等键对应了不同佣金事件", 409)
        return existing, True
    summary = financial_summary(db, tenant_id)
    if event_type == "settlement" and amount_cents > summary["unsettled_commission_cents"]:
        raise FinancialLedgerError("settlement_exceeds_accrual", "确认结算不能超过未结算应计佣金", 409)
    if event_type == "collection" and amount_cents > summary["uncollected_settlement_cents"]:
        raise FinancialLedgerError("collection_exceeds_settlement", "实际收佣不能超过已确认未收结算", 409)
    entry = CommissionLedgerEntry(
        tenant_id=tenant_id,
        event_id=f"COM-{uuid4().hex[:20].upper()}",
        event_type=event_type,
        amount_cents=amount_cents,
        source_recovery_entry_id=None,
        reference=reference,
        idempotency_key=idempotency_key,
        payload_digest=digest,
        created_by=actor_id,
        occurred_at=occurred_at,
    )
    db.add(entry)
    db.flush()
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=f"commission.{event_type}.recorded",
            resource_type="commission_ledger_entry",
            resource_id=entry.event_id,
            detail={"amount_cents": amount_cents, "reference": reference},
        )
    )
    refresh_business_metrics(db, tenant_id)
    db.commit()
    db.refresh(entry)
    return entry, False
