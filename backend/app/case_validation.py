"""Read-only, tenant-scoped evidence for an imported case; never authenticates source data."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from .models import (
    AssetImportBatch,
    AssetPackage,
    CaseFinancialProfile,
    CaseRecord,
    PaymentReceipt,
    RecoveryLedgerEntry,
)


def build_case_validation(db, tenant_id: str, case_id: str, startup) -> dict:
    case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id))
    if case is None:
        raise HTTPException(404, "案件不存在")
    package = db.scalar(
        select(AssetPackage).where(AssetPackage.tenant_id == tenant_id, AssetPackage.package_id == case.package_id)
    )
    profile = db.scalar(
        select(CaseFinancialProfile).where(
            CaseFinancialProfile.tenant_id == tenant_id, CaseFinancialProfile.case_id == case_id
        )
    )
    batch = (
        db.scalar(
            select(AssetImportBatch).where(
                AssetImportBatch.tenant_id == tenant_id, AssetImportBatch.id == case.source_import_batch_id
            )
        )
        if case.source_import_batch_id
        else None
    )
    entries = list(
        db.scalars(
            select(RecoveryLedgerEntry)
            .where(RecoveryLedgerEntry.tenant_id == tenant_id, RecoveryLedgerEntry.case_id == case_id)
            .order_by(RecoveryLedgerEntry.entry_id)
        )
    )
    receipts = {
        r.id: r
        for r in db.scalars(
            select(PaymentReceipt).where(PaymentReceipt.tenant_id == tenant_id, PaymentReceipt.case_id == case_id)
        )
    }
    source_ok = bool(
        batch
        and batch.status == "committed"
        and batch.committed_by
        and batch.committed_by != batch.created_by
        and len(batch.source_digest) == 64
    )
    today = datetime.now(UTC).date()
    checks = [
        {
            "id": "source",
            "label": "独立复核的导入来源",
            "passed": source_ok,
            "detail": "来源摘要可追溯；文件真实性仍需业务方核实"
            if source_ok
            else "样本或未完成独立复核的来源不能用于真实案例验收",
        },
        {
            "id": "mandate",
            "label": "债权与有效委托",
            "passed": bool(
                profile and profile.claim_balance_cents > 0 and profile.mandate_start <= today <= profile.mandate_end
            ),
            "detail": "按 UTC 日期核对债权快照与委托期限",
        },
        {
            "id": "policy",
            "label": "已发布策略",
            "passed": bool(package and package.policy_status == "published"),
            "detail": "新导入资产包仍需独立策略审批",
        },
        {
            "id": "protection",
            "label": "案件保护门禁",
            "passed": not case.blocked,
            "detail": "保护暂停案件不得恢复触达" if case.blocked else "只读取状态，不授予执行权限",
        },
        {
            "id": "outcome",
            "label": "可核对的回款证据链",
            "passed": bool(entries)
            and all(
                (receipt := receipts.get(entry.receipt_id)) is not None
                and receipt.signature_verified
                and receipt.status == "matched"
                and receipt.recovery_entry_id == entry.entry_id
                for entry in entries
            ),
            "detail": "每条账簿记录必须关联验签且已匹配入账的回执；仍需外部银行或服务商核对",
        },
        {
            "id": "environment",
            "label": "生产环境",
            "passed": startup.environment == "production" and not startup.seed_demo_data,
            "detail": "测试环境和模拟 Provider 结果不等于真实业务验证",
        },
    ]
    evidence = {
        "tenant_id": tenant_id,
        "case_id": case_id,
        "case_version": case.version,
        "package_id": case.package_id,
        "policy_version": package.policy_version if package else None,
        "source_digest": batch.source_digest if source_ok else None,
        "checks": checks,
        "ledger": [
            {
                "entry_id": e.entry_id,
                "receipt_id": e.receipt_id,
                "amount_cents": e.amount_cents,
                "event_type": e.event_type,
                "payload_digest": receipts[e.receipt_id].payload_digest if e.receipt_id in receipts else None,
            }
            for e in entries
        ],
        "confirmed_net_recovery_cents": sum(e.amount_cents for e in entries),
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **evidence,
        "generated_at": datetime.now(UTC),
        "report_digest": digest,
        "status": "ready_for_external_review" if all(c["passed"] for c in checks) else "evidence_incomplete",
        "source_authenticity": "requires_external_attestation",
        "real_business_verified": False,
        "enables_external_execution": False,
    }
