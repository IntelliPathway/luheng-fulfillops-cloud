from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from math import ceil

from sqlalchemy import Select, exists, func, literal, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.orm import Session

from .models import (
    AssetPackage,
    CaseFinancialProfile,
    CaseRecord,
    CommissionRule,
    ProtectionIncident,
    RecoveryLedgerEntry,
    RepaymentPlan,
)
from .protection_workflow import ACTIVE_PROTECTION_STATUSES

COMPLETED_CASE_STATUSES = {"已结清", "本期已足额"}
VISIBLE_PLAN_STATUSES = {"pending_review", "active", "completed"}


class AssetCatalogError(RuntimeError):
    def __init__(self, message: str, code: str, http_status: int = 404):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _escaped_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _page_payload(items: list[dict], total: int, page: int, page_size: int, **extra: object) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": ceil(total / page_size) if total else 0,
        **extra,
    }


def _quality_score(
    case_record: CaseRecord,
    profile: CaseFinancialProfile | None,
    active_rule: CommissionRule | None,
) -> int:
    score = 20
    if profile:
        score += 25
        if profile.claim_balance_cents > 0:
            score += 15
        if profile.mandate_start <= profile.mandate_end:
            score += 15
    if active_rule:
        score += 10
    if case_record.contact_basis_ref:
        score += 15
    return min(score, 100)


def _rules_by_id(db: Session, tenant_id: str, rule_ids: set[str]) -> dict[str, CommissionRule]:
    if not rule_ids:
        return {}
    rules = db.scalars(
        select(CommissionRule)
        .where(
            CommissionRule.tenant_id == tenant_id,
            CommissionRule.rule_id.in_(rule_ids),
            CommissionRule.status == "active",
        )
        .order_by(CommissionRule.rule_id, CommissionRule.version.desc())
    )
    result: dict[str, CommissionRule] = {}
    for rule in rules:
        result.setdefault(rule.rule_id, rule)
    return result


def _money_by_case(db: Session, tenant_id: str, case_ids: set[str]) -> dict[str, tuple[int, int]]:
    if not case_ids:
        return {}
    rows = db.execute(
        select(
            RecoveryLedgerEntry.case_id,
            func.coalesce(func.sum(RecoveryLedgerEntry.amount_cents), 0),
            func.coalesce(func.sum(RecoveryLedgerEntry.commission_cents), 0),
        )
        .where(
            RecoveryLedgerEntry.tenant_id == tenant_id,
            RecoveryLedgerEntry.case_id.in_(case_ids),
        )
        .group_by(RecoveryLedgerEntry.case_id)
    )
    return {case_id: (int(recovery), int(commission)) for case_id, recovery, commission in rows}


def _active_protections(db: Session, tenant_id: str, case_ids: set[str]) -> dict[str, ProtectionIncident]:
    if not case_ids:
        return {}
    incidents = db.scalars(
        select(ProtectionIncident)
        .where(
            ProtectionIncident.tenant_id == tenant_id,
            ProtectionIncident.case_id.in_(case_ids),
            ProtectionIncident.status.in_(ACTIVE_PROTECTION_STATUSES),
        )
        .order_by(ProtectionIncident.case_id, ProtectionIncident.opened_at.desc())
    )
    result: dict[str, ProtectionIncident] = {}
    for incident in incidents:
        result.setdefault(incident.case_id, incident)
    return result


def _visible_plans(db: Session, tenant_id: str, case_ids: set[str]) -> dict[str, RepaymentPlan]:
    if not case_ids:
        return {}
    plans = db.scalars(
        select(RepaymentPlan)
        .where(
            RepaymentPlan.tenant_id == tenant_id,
            RepaymentPlan.case_id.in_(case_ids),
            RepaymentPlan.status.in_(VISIBLE_PLAN_STATUSES),
        )
        .order_by(RepaymentPlan.case_id, RepaymentPlan.updated_at.desc())
    )
    result: dict[str, RepaymentPlan] = {}
    for plan in plans:
        result.setdefault(plan.case_id, plan)
    return result


def _next_allowed(
    case_record: CaseRecord,
    package: AssetPackage | None,
    profile: CaseFinancialProfile | None,
    rule: CommissionRule | None,
    protection: ProtectionIncident | None,
    plan: RepaymentPlan | None,
) -> str:
    if case_record.blocked:
        return protection.reason if protection else "等待保护事件复核"
    if case_record.status in COMPLETED_CASE_STATUSES:
        return "仅保留账务与审计记录"
    today = datetime.now(UTC).date()
    if not profile:
        return "补齐债权与委托资料后重新预检"
    if profile.claim_balance_cents <= 0:
        return "核对有效债权余额后重新预检"
    if profile.mandate_start > today:
        return "委托尚未开始，等待生效后重新预检"
    if profile.mandate_end < today:
        return "委托已到期，仅核对被动到账"
    if package and package.policy_status != "published":
        return "资产包策略发布后重新预检"
    if not rule:
        return "补齐有效佣金规则后重新预检"
    if plan and plan.status in {"active", "completed"}:
        return "按已签方案核对下一期"
    if not case_record.contact_basis_ref:
        return "补齐联系依据引用后重新预检"
    return "进入活动资格预检"


def _case_payload(
    case_record: CaseRecord,
    profile: CaseFinancialProfile | None,
    package: AssetPackage | None,
    rule: CommissionRule | None,
    money: tuple[int, int],
    protection: ProtectionIncident | None,
    plan: RepaymentPlan | None,
) -> dict:
    updated_candidates = [case_record.created_at]
    if protection:
        updated_candidates.append(protection.opened_at)
    if plan:
        updated_candidates.append(plan.updated_at)
    return {
        "case_id": case_record.case_id,
        "package_id": case_record.package_id,
        "package_title": package.title if package else None,
        "status": case_record.status,
        "blocked": case_record.blocked,
        "has_signed_plan": case_record.has_signed_plan,
        "data_completeness_score": _quality_score(case_record, profile, rule),
        "claim_balance_cents": profile.claim_balance_cents if profile else None,
        "mandate_start": profile.mandate_start if profile else None,
        "mandate_end": profile.mandate_end if profile else None,
        "commission_rule_id": profile.commission_rule_id if profile else None,
        "commission_rate_bps": rule.rate_bps if rule else None,
        "contact_basis_ref": case_record.contact_basis_ref,
        "source_import_batch_id": case_record.source_import_batch_id,
        "version": case_record.version,
        "created_at": case_record.created_at,
        "updated_at": max(updated_candidates),
        "confirmed_net_recovery_cents": money[0],
        "accrued_commission_cents": money[1],
        "active_protection_id": protection.id if protection else None,
        "protection_category": protection.category if protection else None,
        "protection_reason": protection.reason if protection else None,
        "active_plan_id": plan.plan_id if plan else None,
        "active_plan_status": plan.status if plan else None,
        "next_allowed": _next_allowed(case_record, package, profile, rule, protection, plan),
        "data_source": "server-authoritative",
    }


def _case_filters(
    tenant_id: str,
    *,
    query: str | None,
    package_id: str | None,
    status: str | None,
) -> list:
    filters = [CaseRecord.tenant_id == tenant_id]
    if query:
        pattern = _escaped_pattern(query.strip())
        filters.append(
            or_(
                CaseRecord.case_id.ilike(pattern, escape="\\"),
                CaseRecord.package_id.ilike(pattern, escape="\\"),
                CaseRecord.status.ilike(pattern, escape="\\"),
            )
        )
    if package_id:
        filters.append(CaseRecord.package_id == package_id)
    if status:
        filters.append(CaseRecord.status == status)
    return filters


def _quality_expression(tenant_id: str):
    active_rule_exists = exists(
        select(CommissionRule.id).where(
            CommissionRule.tenant_id == tenant_id,
            CommissionRule.rule_id == CaseFinancialProfile.commission_rule_id,
            CommissionRule.status == "active",
        )
    )
    return (
        literal(20)
        + sql_case((CaseFinancialProfile.id.is_not(None), 25), else_=0)
        + sql_case((CaseFinancialProfile.claim_balance_cents > 0, 15), else_=0)
        + sql_case((CaseFinancialProfile.mandate_start <= CaseFinancialProfile.mandate_end, 15), else_=0)
        + sql_case((active_rule_exists, 10), else_=0)
        + sql_case(
            (
                CaseRecord.contact_basis_ref.is_not(None) & (CaseRecord.contact_basis_ref != ""),
                15,
            ),
            else_=0,
        )
    )


def list_cases(
    db: Session,
    tenant_id: str,
    *,
    query: str | None,
    package_id: str | None,
    status: str | None,
    view: str,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    filters = _case_filters(tenant_id, query=query, package_id=package_id, status=status)
    all_count = int(db.scalar(select(func.count()).select_from(CaseRecord).where(*filters)) or 0)
    facets = {
        "all": all_count,
        "signed": int(
            db.scalar(
                select(func.count()).select_from(CaseRecord).where(*filters, CaseRecord.has_signed_plan.is_(True))
            )
            or 0
        ),
        "blocked": int(
            db.scalar(select(func.count()).select_from(CaseRecord).where(*filters, CaseRecord.blocked.is_(True))) or 0
        ),
    }
    quality_expression = _quality_expression(tenant_id)
    facets["quality"] = int(
        db.scalar(
            select(func.count())
            .select_from(CaseRecord)
            .outerjoin(
                CaseFinancialProfile,
                (CaseFinancialProfile.tenant_id == CaseRecord.tenant_id)
                & (CaseFinancialProfile.case_id == CaseRecord.case_id),
            )
            .where(*filters, quality_expression < 70)
        )
        or 0
    )

    view_filter = None
    if view == "signed":
        view_filter = CaseRecord.has_signed_plan.is_(True)
    elif view == "blocked":
        view_filter = CaseRecord.blocked.is_(True)
    elif view == "quality":
        view_filter = quality_expression < 70

    statement: Select = (
        select(CaseRecord, CaseFinancialProfile)
        .outerjoin(
            CaseFinancialProfile,
            (CaseFinancialProfile.tenant_id == CaseRecord.tenant_id)
            & (CaseFinancialProfile.case_id == CaseRecord.case_id),
        )
        .where(*filters)
    )
    if view_filter is not None:
        statement = statement.where(view_filter)
    total = int(db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    if sort == "created_desc":
        statement = statement.order_by(CaseRecord.created_at.desc(), CaseRecord.case_id)
    elif sort == "balance_desc":
        statement = statement.order_by(CaseFinancialProfile.claim_balance_cents.desc().nullslast(), CaseRecord.case_id)
    else:
        statement = statement.order_by(CaseRecord.case_id)
    rows = list(db.execute(statement.offset((page - 1) * page_size).limit(page_size)))
    case_ids = {case_record.case_id for case_record, _ in rows}
    package_ids = {case_record.package_id for case_record, _ in rows}
    packages = {
        item.package_id: item
        for item in db.scalars(
            select(AssetPackage).where(
                AssetPackage.tenant_id == tenant_id,
                AssetPackage.package_id.in_(package_ids),
            )
        )
    }
    rules = _rules_by_id(
        db,
        tenant_id,
        {profile.commission_rule_id for _, profile in rows if profile},
    )
    money = _money_by_case(db, tenant_id, case_ids)
    protections = _active_protections(db, tenant_id, case_ids)
    plans = _visible_plans(db, tenant_id, case_ids)
    items = [
        _case_payload(
            case_record,
            profile,
            packages.get(case_record.package_id),
            rules.get(profile.commission_rule_id) if profile else None,
            money.get(case_record.case_id, (0, 0)),
            protections.get(case_record.case_id),
            plans.get(case_record.case_id),
        )
        for case_record, profile in rows
    ]
    return _page_payload(items, total, page, page_size, facets=facets)


def get_case(db: Session, tenant_id: str, case_id: str) -> dict:
    row = db.execute(
        select(CaseRecord, CaseFinancialProfile)
        .outerjoin(
            CaseFinancialProfile,
            (CaseFinancialProfile.tenant_id == CaseRecord.tenant_id)
            & (CaseFinancialProfile.case_id == CaseRecord.case_id),
        )
        .where(CaseRecord.tenant_id == tenant_id, CaseRecord.case_id == case_id)
    ).one_or_none()
    if not row:
        raise AssetCatalogError("案件不存在或不属于当前工作空间", "CASE_NOT_FOUND")
    case_record, profile = row
    package = db.scalar(
        select(AssetPackage).where(
            AssetPackage.tenant_id == tenant_id,
            AssetPackage.package_id == case_record.package_id,
        )
    )
    rule = (
        _rules_by_id(db, tenant_id, {profile.commission_rule_id}).get(profile.commission_rule_id) if profile else None
    )
    protection = _active_protections(db, tenant_id, {case_id}).get(case_id)
    plan = _visible_plans(db, tenant_id, {case_id}).get(case_id)
    return _case_payload(
        case_record,
        profile,
        package,
        rule,
        _money_by_case(db, tenant_id, {case_id}).get(case_id, (0, 0)),
        protection,
        plan,
    )


def list_packages(
    db: Session,
    tenant_id: str,
    *,
    query: str | None,
    policy_status: str | None,
    page: int,
    page_size: int,
) -> dict:
    filters = [AssetPackage.tenant_id == tenant_id]
    if query:
        pattern = _escaped_pattern(query.strip())
        filters.append(
            or_(
                AssetPackage.package_id.ilike(pattern, escape="\\"),
                AssetPackage.title.ilike(pattern, escape="\\"),
            )
        )
    if policy_status:
        filters.append(AssetPackage.policy_status == policy_status)
    total = int(db.scalar(select(func.count()).select_from(AssetPackage).where(*filters)) or 0)
    packages = list(
        db.scalars(
            select(AssetPackage)
            .where(*filters)
            .order_by(AssetPackage.created_at.desc(), AssetPackage.package_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    package_ids = {package.package_id for package in packages}
    cases = list(
        db.scalars(
            select(CaseRecord).where(
                CaseRecord.tenant_id == tenant_id,
                CaseRecord.package_id.in_(package_ids),
            )
        )
    )
    case_ids = {case_record.case_id for case_record in cases}
    profiles = {
        profile.case_id: profile
        for profile in db.scalars(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == tenant_id,
                CaseFinancialProfile.case_id.in_(case_ids),
            )
        )
    }
    rules = _rules_by_id(
        db,
        tenant_id,
        {profile.commission_rule_id for profile in profiles.values()},
    )
    money = _money_by_case(db, tenant_id, case_ids)
    cases_by_package: dict[str, list[CaseRecord]] = defaultdict(list)
    for case_record in cases:
        cases_by_package[case_record.package_id].append(case_record)

    items = []
    today = date.today()
    for package in packages:
        package_cases = cases_by_package[package.package_id]
        package_profiles = [profiles[item.case_id] for item in package_cases if item.case_id in profiles]
        scores = [
            _quality_score(
                item,
                profiles.get(item.case_id),
                rules.get(profiles[item.case_id].commission_rule_id) if item.case_id in profiles else None,
            )
            for item in package_cases
        ]
        rates = {
            rules[profile.commission_rule_id].rate_bps
            for profile in package_profiles
            if profile.commission_rule_id in rules
        }
        executable_count = sum(
            1
            for item in package_cases
            if not item.blocked
            and item.status not in COMPLETED_CASE_STATUSES
            and package.policy_status == "published"
            and profiles.get(item.case_id)
            and profiles[item.case_id].claim_balance_cents > 0
            and profiles[item.case_id].mandate_start <= today
            and profiles[item.case_id].mandate_end >= today
            and profiles[item.case_id].commission_rule_id in rules
            and bool(item.contact_basis_ref)
        )
        items.append(
            {
                "package_id": package.package_id,
                "title": package.title,
                "policy_status": package.policy_status,
                "policy_version": package.policy_version,
                "budget_limit_yuan": package.budget_limit_yuan,
                "min_settlement_bps": package.min_settlement_bps,
                "max_installments": package.max_installments,
                "min_down_payment_bps": package.min_down_payment_bps,
                "source_import_batch_id": package.source_import_batch_id,
                "created_at": package.created_at,
                "case_count": len(package_cases),
                "executable_count": executable_count,
                "protected_count": sum(1 for item in package_cases if item.blocked),
                "data_completeness_score": round(sum(scores) / len(scores)) if scores else 0,
                "total_claim_balance_cents": sum(profile.claim_balance_cents for profile in package_profiles),
                "confirmed_net_recovery_cents": sum(money.get(item.case_id, (0, 0))[0] for item in package_cases),
                "accrued_commission_cents": sum(money.get(item.case_id, (0, 0))[1] for item in package_cases),
                "mandate_start": min((profile.mandate_start for profile in package_profiles), default=None),
                "mandate_end": max((profile.mandate_end for profile in package_profiles), default=None),
                "commission_rate_bps": next(iter(rates)) if len(rates) == 1 else None,
                "data_source": "server-authoritative",
            }
        )
    return _page_payload(items, total, page, page_size)
