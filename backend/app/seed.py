from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_ledger import refresh_business_metrics
from .models import (
    Activity,
    AssetPackage,
    BusinessMetricSnapshot,
    CaseFinancialProfile,
    CaseRecord,
    CommissionLedgerEntry,
    CommissionRule,
    IntegrationState,
    PaymentWebhookConfig,
    ProtectionIncident,
    RecoveryLedgerEntry,
    RepaymentInstallment,
    RepaymentPlan,
    ServiceConfig,
    Tenant,
    TenantMembership,
    User,
)
from .repayment_plans import allocate_recovery_to_plan
from .secret_store import store_secret

FINANCIAL_CASES = [
    (
        "TENANT_A",
        "C001",
        "PKG_A",
        "COM_A_V1",
        date(2026, 8, 1),
        date(2026, 12, 31),
        date(2026, 8, 1),
        date(2026, 8, 6),
        True,
    ),
    (
        "TENANT_A",
        "C002",
        "PKG_A",
        "COM_A_V1",
        date(2026, 8, 1),
        date(2026, 12, 31),
        date(2026, 8, 1),
        date(2027, 1, 10),
        True,
    ),
    (
        "TENANT_A",
        "C003",
        "PKG_A",
        "COM_A_V1",
        date(2026, 8, 1),
        date(2026, 12, 31),
        date(2026, 8, 20),
        date(2026, 9, 5),
        True,
    ),
    ("TENANT_A", "C004", "PKG_A", "COM_A_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_A", "C005", "PKG_A", "COM_A_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_A", "C006", "PKG_A", "COM_A_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_A", "C008", "PKG_A", "COM_A_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_A", "C010", "PKG_B", "COM_B_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    (
        "TENANT_A",
        "C014",
        "PKG_B",
        "COM_B_V1",
        date(2026, 8, 1),
        date(2026, 8, 31),
        date(2026, 8, 20),
        date(2027, 1, 25),
        True,
    ),
    (
        "TENANT_A",
        "C015",
        "PKG_B",
        "COM_B_V1",
        date(2026, 1, 1),
        date(2026, 7, 31),
        date(2026, 7, 20),
        date(2026, 12, 25),
        False,
    ),
    ("TENANT_A", "C016", "PKG_B", "COM_B_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_B", "C021", "PKG_C", "COM_C_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
    ("TENANT_B", "C024", "PKG_C", "COM_C_V1", date(2026, 8, 1), date(2026, 12, 31), None, None, False),
]

CLAIM_BALANCE_CENTS = {
    ("TENANT_A", "C001"): 1_200_000,
    ("TENANT_A", "C002"): 1_440_000,
    ("TENANT_A", "C003"): 960_000,
    ("TENANT_A", "C004"): 720_000,
    ("TENANT_A", "C005"): 1_500_000,
    ("TENANT_A", "C006"): 1_560_000,
    ("TENANT_A", "C008"): 1_680_000,
    ("TENANT_A", "C010"): 1_800_000,
    ("TENANT_A", "C014"): 2_040_000,
    ("TENANT_A", "C015"): 2_100_000,
    ("TENANT_A", "C016"): 2_160_000,
    ("TENANT_B", "C021"): 2_460_000,
    ("TENANT_B", "C024"): 2_640_000,
}

SEED_REPAYMENT_PLANS = [
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN001",
        "case_id": "C001",
        "total_cents": 1_000_000,
        "down_payment_cents": 1_000_000,
        "policy_version": 1,
        "signed_at": datetime(2026, 8, 5, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [(date(2026, 8, 6), 1_000_000)],
        "tail_eligible": True,
    },
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN002",
        "case_id": "C002",
        "total_cents": 1_260_000,
        "down_payment_cents": 252_000,
        "policy_version": 1,
        "signed_at": datetime(2026, 8, 5, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [
            (date(2026, 8, 10), 252_000),
            (date(2026, 9, 10), 201_600),
            (date(2026, 10, 10), 201_600),
            (date(2026, 11, 10), 201_600),
            (date(2026, 12, 10), 201_600),
            (date(2027, 1, 10), 201_600),
        ],
        "tail_eligible": True,
    },
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN003",
        "case_id": "C003",
        "total_cents": 800_000,
        "down_payment_cents": 800_000,
        "policy_version": 1,
        "signed_at": datetime(2026, 8, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [(date(2026, 9, 5), 800_000)],
        "tail_eligible": True,
    },
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN006-PENDING",
        "case_id": "C006",
        "status": "pending_review",
        "total_cents": 1_248_000,
        "down_payment_cents": 249_600,
        "policy_version": 1,
        "signed_at": datetime(2026, 9, 14, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [
            (date(2026, 9, 17), 249_600),
            (date(2026, 10, 17), 199_680),
            (date(2026, 11, 17), 199_680),
            (date(2026, 12, 17), 199_680),
            (date(2027, 1, 17), 199_680),
            (date(2027, 2, 17), 199_680),
        ],
        "tail_eligible": True,
    },
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN014",
        "case_id": "C014",
        "total_cents": 1_785_000,
        "down_payment_cents": 357_000,
        "policy_version": 1,
        "signed_at": datetime(2026, 8, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [
            (date(2026, 8, 25), 357_000),
            (date(2026, 9, 25), 285_600),
            (date(2026, 10, 25), 285_600),
            (date(2026, 11, 25), 285_600),
            (date(2026, 12, 25), 285_600),
            (date(2027, 1, 25), 285_600),
        ],
        "tail_eligible": True,
    },
    {
        "tenant_id": "TENANT_A",
        "plan_id": "PLAN015",
        "case_id": "C015",
        "total_cents": 1_837_500,
        "down_payment_cents": 367_500,
        "policy_version": 0,
        "signed_at": datetime(2026, 7, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "schedule": [
            (date(2026, 7, 25), 367_500),
            (date(2026, 8, 25), 294_000),
            (date(2026, 9, 25), 294_000),
            (date(2026, 10, 25), 294_000),
            (date(2026, 11, 25), 294_000),
            (date(2026, 12, 25), 294_000),
        ],
        "tail_eligible": False,
    },
]


SEED_RECOVERIES = [
    (
        "TENANT_A",
        "TX001",
        "C001",
        "PKG_A",
        "2026-08-06",
        "PAYMENT",
        1_000_000,
        1_000_000,
        "COM_A_V1",
        1500,
        150_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "TX002",
        "C002",
        "PKG_A",
        "2026-08-10",
        "PAYMENT",
        252_000,
        252_000,
        "COM_A_V1",
        1500,
        37_800,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "TX003",
        "C002",
        "PKG_A",
        "2026-09-10",
        "PAYMENT",
        100_000,
        100_000,
        "COM_A_V1",
        1500,
        15_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "TX004",
        "C003",
        "PKG_A",
        "2026-09-05",
        "PAYMENT",
        300_000,
        300_000,
        "COM_A_V1",
        1500,
        45_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "TX005",
        "C004",
        "PKG_A",
        "2026-09-07",
        "PAYMENT",
        60_000,
        60_000,
        "COM_A_V1",
        1500,
        9_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "RF005",
        "C004",
        "PKG_A",
        "2026-09-08",
        "REFUND",
        -20_000,
        -20_000,
        "COM_A_V1",
        1500,
        -3_000,
        "REFUND_ORIGINAL_RATE",
        "seed:TX005",
    ),
    (
        "TENANT_A",
        "TX006",
        "C005",
        "PKG_A",
        "2026-09-04",
        "PAYMENT",
        100_000,
        100_000,
        "COM_A_V1",
        1500,
        15_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_A",
        "TX008",
        "C014",
        "PKG_B",
        "2026-09-10",
        "PAYMENT",
        50_000,
        50_000,
        "COM_B_V1",
        1800,
        9_000,
        "SIGNED_PLAN_TAIL",
        None,
    ),
    (
        "TENANT_A",
        "TX009",
        "C015",
        "PKG_B",
        "2026-09-10",
        "PAYMENT",
        80_000,
        0,
        "COM_B_V1",
        1800,
        0,
        "OUTSIDE_TAIL",
        None,
    ),
    (
        "TENANT_A",
        "TX010",
        "C016",
        "PKG_B",
        "2026-07-31",
        "PAYMENT",
        70_000,
        0,
        "COM_B_V1",
        1800,
        0,
        "PRE_MANDATE",
        None,
    ),
    (
        "TENANT_B",
        "TX012",
        "C021",
        "PKG_C",
        "2026-09-09",
        "PAYMENT",
        50_000,
        50_000,
        "COM_C_V1",
        2000,
        10_000,
        "IN_MANDATE",
        None,
    ),
    (
        "TENANT_B",
        "TX013",
        "C021",
        "PKG_C",
        "2026-09-10",
        "PAYMENT",
        30_000,
        30_000,
        "COM_C_V1",
        2000,
        6_000,
        "IN_MANDATE",
        None,
    ),
]


def _seed_financial_data(db: Session) -> None:
    for tenant_id, rule_id, package_id, rate_bps in (
        ("TENANT_A", "COM_A_V1", "PKG_A", 1500),
        ("TENANT_A", "COM_B_V1", "PKG_B", 1800),
        ("TENANT_B", "COM_C_V1", "PKG_C", 2000),
    ):
        exists = db.scalar(
            select(CommissionRule.id).where(
                CommissionRule.tenant_id == tenant_id,
                CommissionRule.rule_id == rule_id,
                CommissionRule.version == 1,
            )
        )
        if not exists:
            db.add(CommissionRule(tenant_id=tenant_id, rule_id=rule_id, package_id=package_id, rate_bps=rate_bps))
    db.flush()
    for tenant_id, case_id, package_id, rule_id, start, end, signed_at, last_due, tail_eligible in FINANCIAL_CASES:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id))
        if not case:
            case = CaseRecord(
                tenant_id=tenant_id,
                case_id=case_id,
                package_id=package_id,
                status="已确认回款",
                contact_basis_ref=f"DEMO-CONSENT-{case_id}",
            )
            db.add(case)
            db.flush()
        elif not case.contact_basis_ref:
            case.contact_basis_ref = f"DEMO-CONSENT-{case_id}"
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == tenant_id,
                CaseFinancialProfile.case_id == case_id,
            )
        )
        claim_balance_cents = CLAIM_BALANCE_CENTS[(tenant_id, case_id)]
        if not profile:
            db.add(
                CaseFinancialProfile(
                    tenant_id=tenant_id,
                    case_id=case_id,
                    commission_rule_id=rule_id,
                    claim_balance_cents=claim_balance_cents,
                    mandate_start=start,
                    mandate_end=end,
                    signed_plan_at=signed_at,
                    signed_plan_last_due=last_due,
                    signed_plan_tail_eligible=tail_eligible,
                )
            )
        elif profile.claim_balance_cents <= 1:
            profile.claim_balance_cents = claim_balance_cents
    db.flush()
    for row in SEED_RECOVERIES:
        (
            tenant_id,
            transaction_id,
            case_id,
            package_id,
            booked,
            event_type,
            amount,
            eligible,
            rule_id,
            rate,
            commission,
            reason,
            original,
        ) = row
        entry_id = f"seed:{transaction_id}"
        exists = db.scalar(
            select(RecoveryLedgerEntry.id).where(
                RecoveryLedgerEntry.tenant_id == tenant_id,
                RecoveryLedgerEntry.entry_id == entry_id,
            )
        )
        if exists:
            continue
        booked_at = datetime.fromisoformat(f"{booked}T12:00:00")
        db.add(
            RecoveryLedgerEntry(
                entry_id=entry_id,
                tenant_id=tenant_id,
                receipt_id=None,
                case_id=case_id,
                package_id=package_id,
                event_type=event_type,
                amount_cents=amount,
                eligible_amount_cents=eligible,
                commission_rule_id=rule_id,
                commission_rule_version=1,
                rate_bps=rate,
                commission_cents=commission,
                reason=reason,
                original_entry_id=original,
                allocation=f"{case_id} · 演示账簿迁移",
                source="AMC 脱敏回款文件",
                booked_at=booked_at,
            )
        )
        digest = hashlib.sha256(f"{entry_id}:{commission}".encode()).hexdigest()
        db.add(
            CommissionLedgerEntry(
                tenant_id=tenant_id,
                event_id=f"SEED-COM-{transaction_id}",
                event_type="accrual" if commission >= 0 else "reversal",
                amount_cents=commission,
                source_recovery_entry_id=entry_id,
                reference=f"回款账簿 {entry_id}",
                idempotency_key=f"seed:{transaction_id}",
                payload_digest=digest,
                created_by="system:seed",
                occurred_at=booked_at,
            )
        )
    sandbox_secret = os.getenv("PAYMENT_SANDBOX_SECRET", "").strip()
    if sandbox_secret:
        provider = "sandbox-amc"
        config = db.scalar(
            select(PaymentWebhookConfig).where(
                PaymentWebhookConfig.tenant_id == "TENANT_A",
                PaymentWebhookConfig.provider == provider,
            )
        )
        if not config:
            secret_ref, last4 = store_secret(db, "TENANT_A", f"payment-{provider}", sandbox_secret)
            db.add(
                PaymentWebhookConfig(
                    tenant_id="TENANT_A",
                    provider=provider,
                    secret_ref=secret_ref,
                    credential_last4=last4,
                    version=1,
                    max_amount_cents=100_000_000,
                )
            )
    db.flush()
    refresh_business_metrics(db, "TENANT_A")
    refresh_business_metrics(db, "TENANT_B")


def _seed_repayment_data(db: Session) -> None:
    for item in SEED_REPAYMENT_PLANS:
        plan_status = item.get("status", "active")
        plan = db.scalar(
            select(RepaymentPlan).where(
                RepaymentPlan.tenant_id == item["tenant_id"],
                RepaymentPlan.plan_id == item["plan_id"],
            )
        )
        if not plan:
            evidence_digest = hashlib.sha256(f"seed-plan:{item['tenant_id']}:{item['plan_id']}".encode()).hexdigest()
            agreement_digest = hashlib.sha256(f"mock-agreement:{item['plan_id']}".encode()).hexdigest()
            plan = RepaymentPlan(
                tenant_id=item["tenant_id"],
                plan_id=item["plan_id"],
                case_id=item["case_id"],
                status=plan_status,
                version=1 if plan_status == "pending_review" else 2,
                claim_balance_cents=CLAIM_BALANCE_CENTS[(item["tenant_id"], item["case_id"])],
                total_cents=item["total_cents"],
                down_payment_cents=item["down_payment_cents"],
                installment_count=len(item["schedule"]),
                policy_version=item["policy_version"],
                policy_snapshot={
                    "package_id": "PKG_A" if item["case_id"] in {"C001", "C002", "C003", "C006"} else "PKG_B",
                    "policy_version": item["policy_version"],
                    "min_settlement_bps": 7000,
                    "max_installments": 6,
                    "min_down_payment_bps": 2000,
                    "seed": True,
                },
                agreement_reference=f"AGREEMENT-{item['plan_id']}",
                agreement_digest=agreement_digest,
                signed_at=item["signed_at"],
                evidence_digest=evidence_digest,
                proposal_reason="既有已签方案迁移为服务端权威台账",
                proposed_by="system:seed",
                proposed_at=item["signed_at"],
                reviewed_by=None if plan_status == "pending_review" else "system:migration",
                reviewed_at=None if plan_status == "pending_review" else item["signed_at"],
                review_note=None if plan_status == "pending_review" else "迁移已核验的演示协议摘要",
                activated_at=None if plan_status == "pending_review" else item["signed_at"],
            )
            db.add(plan)
            db.flush()
            for index, (due_date, due_cents) in enumerate(item["schedule"], start=1):
                db.add(
                    RepaymentInstallment(
                        tenant_id=item["tenant_id"],
                        plan_row_id=plan.id,
                        installment_id=f"{item['plan_id']}-{index:02d}",
                        installment_no=index,
                        due_date=due_date,
                        due_cents=due_cents,
                    )
                )
        case = db.scalar(
            select(CaseRecord).where(
                CaseRecord.tenant_id == item["tenant_id"],
                CaseRecord.case_id == item["case_id"],
            )
        )
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == item["tenant_id"],
                CaseFinancialProfile.case_id == item["case_id"],
            )
        )
        if case and plan_status != "pending_review":
            case.has_signed_plan = True
        if profile and plan_status != "pending_review":
            profile.signed_plan_at = item["signed_at"].date()
            profile.signed_plan_last_due = item["schedule"][-1][0]
            profile.signed_plan_tail_eligible = item["tail_eligible"]
    db.flush()
    recoveries = list(
        db.scalars(
            select(RecoveryLedgerEntry)
            .where(RecoveryLedgerEntry.tenant_id == "TENANT_A")
            .order_by(RecoveryLedgerEntry.booked_at, RecoveryLedgerEntry.created_at)
        )
    )
    for recovery in recoveries:
        allocate_recovery_to_plan(db, recovery)


def _seed_protection_data(db: Session) -> None:
    rows = [
        {
            "tenant_id": "TENANT_A",
            "case_id": "C010",
            "source_event_id": "GUARD-DEMO-C010",
            "category": "debt_dispute",
            "priority": "P0",
            "reason": "存在未解决异议，触达已暂停",
            "owner": "AMC 异议专员",
            "release_policy": "maker_checker",
            "status": "pending_review",
            "version": 2,
            "previous_case_status": "待联系",
            "sla_due_at": datetime(2026, 9, 15, 16, 0, tzinfo=UTC).replace(tzinfo=None),
            "opened_by": "system:guard",
            "opened_at": datetime(2026, 9, 15, 9, 45, tzinfo=UTC).replace(tzinfo=None),
            "resolution_note": "异议金额与债权资料已复核，处理回执已归档，申请重新评估案件。",
            "evidence_refs": ["EVIDENCE-DISPUTE-C010-01"],
            "proposed_by": "test-operator",
            "proposed_at": datetime(2026, 9, 15, 11, 20, tzinfo=UTC).replace(tzinfo=None),
        },
        {
            "tenant_id": "TENANT_A",
            "case_id": "C014",
            "source_event_id": "GUARD-DEMO-C014",
            "category": "mandate_expired",
            "priority": "P1",
            "reason": "委托已到期，禁止新的主动触达",
            "owner": "合同管理员",
            "release_policy": "renewal_evidence",
            "status": "open",
            "version": 1,
            "previous_case_status": "履约中",
            "sla_due_at": datetime(2026, 9, 16, 9, 0, tzinfo=UTC).replace(tzinfo=None),
            "opened_by": "system:mandate-guard",
            "opened_at": datetime(2026, 9, 15, 9, 0, tzinfo=UTC).replace(tzinfo=None),
            "resolution_note": None,
            "evidence_refs": [],
            "proposed_by": None,
            "proposed_at": None,
        },
    ]
    for row in rows:
        exists = db.scalar(
            select(ProtectionIncident.id).where(
                ProtectionIncident.tenant_id == row["tenant_id"],
                ProtectionIncident.source_event_id == row["source_event_id"],
            )
        )
        if exists:
            continue
        opening_digest = hashlib.sha256(
            json.dumps(
                {
                    "tenant_id": row["tenant_id"],
                    "case_id": row["case_id"],
                    "source_event_id": row["source_event_id"],
                    "category": row["category"],
                    "reason": row["reason"],
                    "owner": row["owner"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        evidence_digest = None
        if row["evidence_refs"]:
            evidence_digest = hashlib.sha256(
                json.dumps(
                    {
                        "opening_digest": opening_digest,
                        "resolution_note": row["resolution_note"],
                        "evidence_refs": row["evidence_refs"],
                        "version": row["version"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        db.add(
            ProtectionIncident(
                **row,
                opening_digest=opening_digest,
                evidence_digest=evidence_digest,
            )
        )


def seed_demo_data(db: Session) -> None:
    existing_tenants = set(db.scalars(select(Tenant.id)))
    demo_tenants = {"TENANT_A", "TENANT_B"}
    if existing_tenants and not demo_tenants.issubset(existing_tenants):
        raise RuntimeError("数据库已包含非演示租户，拒绝隐式混入 AMC 演示数据")
    if existing_tenants:
        demo_users = {
            "Terry": ("terry@example.test", "Terry"),
            "test-user": ("admin@example.test", "测试管理员"),
            "test-operator": ("operator@example.test", "测试运营"),
            "test-viewer": ("viewer@example.test", "测试观察员"),
        }
        for user_id, (email, display_name) in demo_users.items():
            if not db.get(User, user_id):
                db.add(User(id=user_id, email=email, display_name=display_name))
        db.flush()
        for tenant_id in ("TENANT_A", "TENANT_B"):
            for user_id, role in (
                ("Terry", "admin"),
                ("test-user", "admin"),
                ("test-operator", "operator"),
                ("test-viewer", "viewer"),
            ):
                exists = db.scalar(
                    select(TenantMembership.id).where(
                        TenantMembership.tenant_id == tenant_id,
                        TenantMembership.user_id == user_id,
                    )
                )
                if not exists:
                    db.add(TenantMembership(tenant_id=tenant_id, user_id=user_id, role=role))
        metric_values = {
            "TENANT_A": {
                "confirmed_net_recovery": 19920,
                "commission_eligible_recovery": 18420,
                "accrued_commission": 2778,
                "settled_commission": 0,
                "collected_commission": 0,
            },
            "TENANT_B": {
                "confirmed_net_recovery": 800,
                "commission_eligible_recovery": 800,
                "accrued_commission": 160,
                "settled_commission": 0,
                "collected_commission": 0,
            },
        }
        for tenant_id, values in metric_values.items():
            for key, value in values.items():
                exists = db.scalar(
                    select(BusinessMetricSnapshot.id).where(
                        BusinessMetricSnapshot.tenant_id == tenant_id,
                        BusinessMetricSnapshot.metric_key == key,
                    )
                )
                if not exists:
                    db.add(
                        BusinessMetricSnapshot(
                            tenant_id=tenant_id, metric_key=key, value=value, source="已确认回款与佣金账本（演示快照）"
                        )
                    )
        upgrade_activities = [
            ("TENANT_A", "ACT-003", "已结清归档", "PKG_A", "履约归档", "completed", ["C001"]),
            ("TENANT_A", "ACT-004", "异议案件保护", "PKG_B", "异议保护", "blocked", ["C010"]),
            ("TENANT_A", "ACT-005", "委托到期管理", "PKG_B", "尾期回款核对", "blocked", ["C014"]),
            ("TENANT_B", "ACT-006", "确认回款归集", "PKG_C", "回款自动核对", "running", ["C021"]),
        ]
        for tenant_id, activity_id, name, package_id, goal, activity_status, case_ids in upgrade_activities:
            exists = db.scalar(
                select(Activity.id).where(Activity.tenant_id == tenant_id, Activity.activity_id == activity_id)
            )
            if not exists:
                db.add(
                    Activity(
                        tenant_id=tenant_id,
                        activity_id=activity_id,
                        name=name,
                        package_id=package_id,
                        goal=goal,
                        status=activity_status,
                        mode="sandbox",
                        budget_yuan=30,
                        case_ids=case_ids,
                        policy_version=1,
                        service_snapshot={},
                        preflight={"seed": True},
                    )
                )
        _seed_financial_data(db)
        _seed_repayment_data(db)
        _seed_protection_data(db)
        db.commit()
        return

    db.add_all([Tenant(id="TENANT_A", name="租户 A"), Tenant(id="TENANT_B", name="租户 B")])
    db.add_all(
        [
            User(id="Terry", email="terry@example.test", display_name="Terry"),
            User(id="test-user", email="admin@example.test", display_name="测试管理员"),
            User(id="test-operator", email="operator@example.test", display_name="测试运营"),
            User(id="test-viewer", email="viewer@example.test", display_name="测试观察员"),
        ]
    )
    db.flush()
    for tenant_id in ("TENANT_A", "TENANT_B"):
        db.add_all(
            [
                TenantMembership(tenant_id=tenant_id, user_id="Terry", role="admin"),
                TenantMembership(tenant_id=tenant_id, user_id="test-user", role="admin"),
                TenantMembership(tenant_id=tenant_id, user_id="test-operator", role="operator"),
                TenantMembership(tenant_id=tenant_id, user_id="test-viewer", role="viewer"),
            ]
        )
    db.add_all(
        [
            AssetPackage(
                tenant_id="TENANT_A",
                package_id="PKG_A",
                title="长龄个贷一期",
                policy_status="published",
                policy_version=1,
                budget_limit_yuan=30,
            ),
            AssetPackage(
                tenant_id="TENANT_A",
                package_id="PKG_B",
                title="长龄个贷二期",
                policy_status="published",
                policy_version=1,
                budget_limit_yuan=30,
            ),
            AssetPackage(
                tenant_id="TENANT_B",
                package_id="PKG_C",
                title="消费个贷三期",
                policy_status="published",
                policy_version=1,
                budget_limit_yuan=30,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            CaseRecord(tenant_id="TENANT_A", case_id="C001", package_id="PKG_A", status="已结清", has_signed_plan=True),
            CaseRecord(tenant_id="TENANT_A", case_id="C002", package_id="PKG_A", status="履约中", has_signed_plan=True),
            CaseRecord(
                tenant_id="TENANT_A", case_id="C003", package_id="PKG_A", status="部分履约", has_signed_plan=True
            ),
            CaseRecord(tenant_id="TENANT_A", case_id="C006", package_id="PKG_A", status="到账待匹配"),
            CaseRecord(tenant_id="TENANT_A", case_id="C008", package_id="PKG_A", status="待联系"),
            CaseRecord(tenant_id="TENANT_A", case_id="C010", package_id="PKG_B", status="异议暂停", blocked=True),
            CaseRecord(
                tenant_id="TENANT_A",
                case_id="C014",
                package_id="PKG_B",
                status="委托到期",
                blocked=True,
                has_signed_plan=True,
            ),
            CaseRecord(tenant_id="TENANT_B", case_id="C021", package_id="PKG_C", status="已确认回款"),
            CaseRecord(tenant_id="TENANT_B", case_id="C024", package_id="PKG_C", status="待联系"),
        ]
    )
    # Composite tenant foreign keys require packages and cases to be durable before
    # dependent activity, commission and financial rows are flushed.
    db.flush()

    configs = [
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="agent",
            provider="Hermes Agent",
            settings={
                "endpoint": "http://agent-gateway.internal/v1",
                "profile": "fulfill-agent-v3",
                "approval": "高影响动作需确认",
            },
            secret_ref="kms://luheng/TENANT_A/agent/seed",
            credential_last4="DEMO",
            version=3,
            connected=True,
            latency_ms=126,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="model",
            provider="DeepSeek",
            settings={
                "endpoint": "https://api.deepseek.com",
                "model": "deepseek-flash",
                "timeout": "30",
                "executionMode": "contract-only",
                "maxOutputTokens": "512",
                "maxCostUsd": "0.05",
            },
            secret_ref="kms://luheng/TENANT_A/model/seed",
            credential_last4="DEMO",
            version=2,
            connected=True,
            latency_ms=816,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="voice",
            provider="阿里云智能语音",
            settings={
                "region": "华东 2（上海）",
                "asr": "Paraformer 实时版",
                "tts": "CosyVoice · 龙橙",
                "sampleRate": "16 kHz",
            },
            secret_ref="kms://luheng/TENANT_A/voice/seed",
            credential_last4="DEMO",
            version=1,
            connected=False,
        ),
        ServiceConfig(
            tenant_id="TENANT_A",
            service_type="phone",
            provider="LiveKit SIP",
            settings={
                "sipHost": "sip.example.test:5061",
                "trunk": "amc-demo-trunk",
                "callerId": "010****8800",
                "callback": "https://example.test/telephony/events",
            },
            secret_ref="kms://luheng/TENANT_A/phone/seed",
            credential_last4="DEMO",
            version=1,
            connected=False,
        ),
    ]
    db.add_all(configs)
    db.add_all(
        [
            IntegrationState(tenant_id="TENANT_A", invalidated_reason="语音与电话服务尚未完成连接测试"),
            IntegrationState(tenant_id="TENANT_B", invalidated_reason="尚未完成服务配置"),
        ]
    )
    db.add_all(
        [
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-001",
                name="履约补款跟进",
                package_id="PKG_A",
                goal="已签协议履约",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C002"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-002",
                name="一次性方案履约",
                package_id="PKG_A",
                goal="已签协议履约",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C003"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-003",
                name="已结清归档",
                package_id="PKG_A",
                goal="履约归档",
                status="completed",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C001"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-004",
                name="异议案件保护",
                package_id="PKG_B",
                goal="异议保护",
                status="blocked",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C010"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_A",
                activity_id="ACT-005",
                name="委托到期管理",
                package_id="PKG_B",
                goal="尾期回款核对",
                status="blocked",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C014"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
            Activity(
                tenant_id="TENANT_B",
                activity_id="ACT-006",
                name="确认回款归集",
                package_id="PKG_C",
                goal="回款自动核对",
                status="running",
                mode="sandbox",
                budget_yuan=30,
                case_ids=["C021"],
                policy_version=1,
                service_snapshot={},
                preflight={"seed": True},
            ),
        ]
    )
    metric_values = {
        "TENANT_A": {
            "confirmed_net_recovery": 19920,
            "commission_eligible_recovery": 18420,
            "accrued_commission": 2778,
            "settled_commission": 0,
            "collected_commission": 0,
        },
        "TENANT_B": {
            "confirmed_net_recovery": 800,
            "commission_eligible_recovery": 800,
            "accrued_commission": 160,
            "settled_commission": 0,
            "collected_commission": 0,
        },
    }
    for tenant_id, values in metric_values.items():
        db.add_all(
            BusinessMetricSnapshot(
                tenant_id=tenant_id,
                metric_key=key,
                value=value,
                source="已确认回款与佣金账本（演示快照）",
            )
            for key, value in values.items()
        )
    _seed_financial_data(db)
    _seed_repayment_data(db)
    _seed_protection_data(db)
    db.commit()
