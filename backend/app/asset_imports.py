from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import (
    AssetImportBatch,
    AssetPackage,
    CaseFinancialProfile,
    CaseRecord,
    CommissionRule,
)

SCHEMA_VERSION = "asset-case-v1"
MAX_ROWS = 1000
REQUIRED_FIELDS = {
    "package_id",
    "package_title",
    "case_id",
    "claim_balance_cents",
    "mandate_start",
    "mandate_end",
    "commission_rule_id",
    "commission_rate_bps",
    "contact_basis_ref",
}
OPTIONAL_FIELDS = {"case_status"}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
FORBIDDEN_PII_FIELDS = {
    "address",
    "borrower_name",
    "email",
    "id_card",
    "id_number",
    "name",
    "phone",
    "phone_number",
    "姓名",
    "手机号",
    "身份证号",
    "地址",
    "邮箱",
}
ALLOWED_CASE_STATUSES = {"待联系", "资料待补", "授权待补", "金额待核", "身份待核"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
TITLE_RE = re.compile(r"^[A-Za-z0-9\u3400-\u9fff _()（）-]+$")


class AssetImportError(RuntimeError):
    def __init__(self, message: str, code: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _issue(
    code: str,
    message: str,
    *,
    row_number: int | None = None,
    field: str | None = None,
    severity: str = "error",
) -> dict:
    return {
        "row_number": row_number,
        "severity": severity,
        "code": code,
        "field": field,
        "message": message,
    }


def _parse_integer(
    value: str, field: str, row_number: int, minimum: int, maximum: int
) -> tuple[int | None, dict | None]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, _issue("INVALID_INTEGER", "必须是整数", row_number=row_number, field=field)
    if parsed < minimum or parsed > maximum:
        return None, _issue(
            "INTEGER_OUT_OF_RANGE",
            f"必须在 {minimum}–{maximum} 之间",
            row_number=row_number,
            field=field,
        )
    return parsed, None


def _parse_date(value: str, field: str, row_number: int) -> tuple[date | None, dict | None]:
    try:
        return date.fromisoformat(value), None
    except (TypeError, ValueError):
        return None, _issue("INVALID_DATE", "必须使用 YYYY-MM-DD", row_number=row_number, field=field)


def _validate_identifier(value: str, field: str, row_number: int, maximum: int) -> dict | None:
    if not 4 <= len(value) <= maximum or not IDENTIFIER_RE.fullmatch(value):
        return _issue(
            "INVALID_IDENTIFIER",
            f"必须为 4–{maximum} 位字母、数字、点、下划线、冒号或连字符",
            row_number=row_number,
            field=field,
        )
    return None


def _parse_csv(csv_text: str) -> tuple[list[dict], list[dict], int]:
    issues: list[dict] = []
    normalized_rows: list[dict] = []
    text = csv_text.removeprefix("\ufeff")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        records = list(reader)
    except csv.Error:
        return [], [_issue("MALFORMED_CSV", "CSV 引号或分隔符格式不合法")], 0
    records = [row for row in records if any(cell.strip() for cell in row)]
    if not records:
        return [], [_issue("EMPTY_FILE", "CSV 不包含表头和数据")], 0
    headers = [value.strip().removeprefix("\ufeff") for value in records[0]]
    if len(headers) != len(set(headers)):
        issues.append(_issue("DUPLICATE_HEADER", "CSV 表头存在重复字段"))
    forbidden = sorted({header for header in headers if header.lower() in FORBIDDEN_PII_FIELDS})
    if forbidden:
        issues.append(
            _issue(
                "PII_FIELD_FORBIDDEN",
                f"该通道不接收个人敏感字段：{'、'.join(forbidden)}",
            )
        )
    missing = sorted(REQUIRED_FIELDS - set(headers))
    if missing:
        issues.append(_issue("MISSING_HEADER", f"缺少必填字段：{'、'.join(missing)}"))
    unknown = sorted(set(headers) - ALLOWED_FIELDS - FORBIDDEN_PII_FIELDS)
    if unknown:
        issues.append(_issue("UNKNOWN_HEADER", f"存在未纳入 v1 契约的字段：{'、'.join(unknown)}"))
    row_count = max(0, len(records) - 1)
    if row_count > MAX_ROWS:
        issues.append(_issue("ROW_LIMIT_EXCEEDED", f"单批最多 {MAX_ROWS} 行，当前为 {row_count} 行"))
    if issues:
        return [], issues, row_count

    seen_case_ids: set[str] = set()
    package_titles: dict[str, str] = {}
    rules: dict[str, tuple[str, int]] = {}
    for index, values in enumerate(records[1 : MAX_ROWS + 1], start=2):
        if len(values) != len(headers):
            issues.append(
                _issue(
                    "COLUMN_COUNT_MISMATCH",
                    f"列数应为 {len(headers)}，实际为 {len(values)}",
                    row_number=index,
                )
            )
            continue
        row = {header: value.strip() for header, value in zip(headers, values, strict=True)}
        row_issues: list[dict] = []
        for field in REQUIRED_FIELDS:
            if not row[field]:
                row_issues.append(_issue("REQUIRED_VALUE", "不能为空", row_number=index, field=field))
        for field, maximum in (("package_id", 40), ("case_id", 40), ("commission_rule_id", 80)):
            if row[field] and (problem := _validate_identifier(row[field], field, index, maximum)):
                row_issues.append(problem)
        title = row["package_title"]
        if title and (len(title) > 120 or not TITLE_RE.fullmatch(title) or title[0] in "=+-@"):
            row_issues.append(
                _issue(
                    "INVALID_TITLE",
                    "仅允许 1–120 位中英文、数字、空格和安全标点",
                    row_number=index,
                    field="package_title",
                )
            )
        reference = row["contact_basis_ref"]
        if reference and (len(reference) > 120 or not REFERENCE_RE.fullmatch(reference)):
            row_issues.append(
                _issue(
                    "INVALID_REFERENCE",
                    "必须是 4–120 位安全证据引用，不得填写联系方式正文",
                    row_number=index,
                    field="contact_basis_ref",
                )
            )
        elif reference and len(reference) < 4:
            row_issues.append(
                _issue(
                    "INVALID_REFERENCE",
                    "证据引用至少 4 位",
                    row_number=index,
                    field="contact_basis_ref",
                )
            )
        balance, balance_issue = _parse_integer(
            row["claim_balance_cents"], "claim_balance_cents", index, 1, 1_000_000_000
        )
        rate, rate_issue = _parse_integer(row["commission_rate_bps"], "commission_rate_bps", index, 1, 10_000)
        mandate_start, start_issue = _parse_date(row["mandate_start"], "mandate_start", index)
        mandate_end, end_issue = _parse_date(row["mandate_end"], "mandate_end", index)
        row_issues.extend(problem for problem in (balance_issue, rate_issue, start_issue, end_issue) if problem)
        if mandate_start and mandate_end:
            if mandate_end < mandate_start:
                row_issues.append(
                    _issue(
                        "INVALID_MANDATE_RANGE",
                        "委托截止日期不能早于开始日期",
                        row_number=index,
                        field="mandate_end",
                    )
                )
            if mandate_end < datetime.now(UTC).date():
                row_issues.append(
                    _issue(
                        "MANDATE_EXPIRED",
                        "委托已经到期，不能通过普通导入进入可运营案件池",
                        row_number=index,
                        field="mandate_end",
                    )
                )
        status = row.get("case_status") or "待联系"
        if status not in ALLOWED_CASE_STATUSES:
            row_issues.append(
                _issue(
                    "UNSAFE_INITIAL_STATUS",
                    f"初始状态只允许：{'、'.join(sorted(ALLOWED_CASE_STATUSES))}",
                    row_number=index,
                    field="case_status",
                )
            )
        if row["case_id"] in seen_case_ids:
            row_issues.append(_issue("DUPLICATE_IN_FILE", "同一批次中案件编号重复", row_number=index, field="case_id"))
        seen_case_ids.add(row["case_id"])
        previous_title = package_titles.setdefault(row["package_id"], title)
        if previous_title != title:
            row_issues.append(
                _issue(
                    "PACKAGE_TITLE_CONFLICT",
                    "同一资产包编号对应了不同名称",
                    row_number=index,
                    field="package_title",
                )
            )
        if rate is not None:
            previous_rule = rules.setdefault(row["commission_rule_id"], (row["package_id"], rate))
            if previous_rule != (row["package_id"], rate):
                row_issues.append(
                    _issue(
                        "COMMISSION_RULE_CONFLICT",
                        "同一佣金规则编号对应了不同资产包或比例",
                        row_number=index,
                        field="commission_rule_id",
                    )
                )
        if row_issues:
            issues.extend(row_issues)
            continue
        normalized_rows.append(
            {
                "row_number": index,
                "package_id": row["package_id"],
                "package_title": title,
                "case_id": row["case_id"],
                "case_status": status,
                "claim_balance_cents": balance,
                "mandate_start": mandate_start.isoformat(),
                "mandate_end": mandate_end.isoformat(),
                "commission_rule_id": row["commission_rule_id"],
                "commission_rate_bps": rate,
                "contact_basis_ref": reference,
            }
        )
    return normalized_rows, issues, row_count


def _batch_payload(batch: AssetImportBatch, *, idempotent_replay: bool = False) -> dict:
    return {
        "id": batch.id,
        "source_filename": batch.source_filename,
        "source_digest": batch.source_digest,
        "schema_version": batch.schema_version,
        "status": batch.status,
        "version": batch.version,
        "row_count": batch.row_count,
        "valid_count": batch.valid_count,
        "invalid_count": batch.invalid_count,
        "duplicate_count": batch.duplicate_count,
        "package_count": batch.package_count,
        "total_claim_balance_cents": batch.total_claim_balance_cents,
        "issues": batch.issues,
        "created_by": batch.created_by,
        "created_at": batch.created_at,
        "committed_by": batch.committed_by,
        "committed_at": batch.committed_at,
        "review_note": batch.review_note,
        "idempotent_replay": idempotent_replay,
    }


def _existing_rules_by_id(
    db: Session,
    tenant_id: str,
    rule_ids: set[str],
) -> dict[str, list[CommissionRule]]:
    rules: dict[str, list[CommissionRule]] = {}
    for rule in db.scalars(
        select(CommissionRule).where(
            CommissionRule.tenant_id == tenant_id,
            CommissionRule.rule_id.in_(rule_ids),
        )
    ):
        rules.setdefault(rule.rule_id, []).append(rule)
    return rules


def _find_matching_active_rule(rules: list[CommissionRule], row: dict) -> CommissionRule | None:
    return next(
        (
            rule
            for rule in rules
            if rule.status == "active"
            and rule.package_id == row["package_id"]
            and rule.rate_bps == row["commission_rate_bps"]
        ),
        None,
    )


def create_import_preview(
    db: Session,
    tenant_id: str,
    actor_id: str,
    *,
    filename: str,
    csv_text: str,
    idempotency_key: str,
) -> dict:
    source_digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    existing = db.scalar(
        select(AssetImportBatch).where(
            AssetImportBatch.tenant_id == tenant_id,
            AssetImportBatch.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.source_digest != source_digest:
            raise AssetImportError("同一幂等键不能对应不同文件", "IDEMPOTENCY_CONFLICT")
        return _batch_payload(existing, idempotent_replay=True)

    normalized_rows, issues, row_count = _parse_csv(csv_text)
    existing_cases = set(db.scalars(select(CaseRecord.case_id).where(CaseRecord.tenant_id == tenant_id)).all())
    package_ids = {str(row["package_id"]) for row in normalized_rows}
    existing_packages = {
        row.package_id: row
        for row in db.scalars(
            select(AssetPackage).where(
                AssetPackage.tenant_id == tenant_id,
                AssetPackage.package_id.in_(package_ids),
            )
        )
    }
    rule_ids = {str(row["commission_rule_id"]) for row in normalized_rows}
    existing_rules = _existing_rules_by_id(db, tenant_id, rule_ids)
    accepted_rows: list[dict] = []
    duplicate_count = 0
    for row in normalized_rows:
        if row["case_id"] in existing_cases:
            duplicate_count += 1
            issues.append(
                _issue(
                    "EXISTING_CASE_SKIPPED",
                    "当前工作空间已存在该案件，提交时不会覆盖",
                    row_number=row["row_number"],
                    field="case_id",
                    severity="warning",
                )
            )
            continue
        package = existing_packages.get(row["package_id"])
        if package and package.title != row["package_title"]:
            issues.append(
                _issue(
                    "EXISTING_PACKAGE_CONFLICT",
                    "资产包编号已存在，但名称与文件不一致",
                    row_number=row["row_number"],
                    field="package_title",
                )
            )
            continue
        rule_versions = existing_rules.get(row["commission_rule_id"], [])
        if rule_versions and not _find_matching_active_rule(rule_versions, row):
            issues.append(
                _issue(
                    "EXISTING_COMMISSION_RULE_CONFLICT",
                    "佣金规则编号已存在，但没有与文件一致的启用版本",
                    row_number=row["row_number"],
                    field="commission_rule_id",
                )
            )
            continue
        accepted_rows.append(row)

    invalid_rows = {issue["row_number"] for issue in issues if issue["severity"] == "error" and issue["row_number"]}
    has_global_error = any(issue["severity"] == "error" and issue["row_number"] is None for issue in issues)
    valid_count = len(accepted_rows)
    status = "blocked" if has_global_error or invalid_rows or not valid_count else "ready"
    package_count = len({row["package_id"] for row in accepted_rows})
    batch = AssetImportBatch(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        source_filename=filename,
        source_digest=source_digest,
        schema_version=SCHEMA_VERSION,
        status=status,
        row_count=row_count,
        valid_count=valid_count,
        invalid_count=len(invalid_rows) if invalid_rows else (row_count if has_global_error else 0),
        duplicate_count=duplicate_count,
        package_count=package_count,
        total_claim_balance_cents=sum(int(row["claim_balance_cents"]) for row in accepted_rows),
        normalized_rows=accepted_rows,
        issues=issues,
        created_by=actor_id,
    )
    db.add(batch)
    db.flush()
    return _batch_payload(batch)


def list_import_batches(db: Session, tenant_id: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(AssetImportBatch)
        .where(AssetImportBatch.tenant_id == tenant_id)
        .order_by(AssetImportBatch.created_at.desc())
        .limit(limit)
    )
    return [_batch_payload(row) for row in rows]


def commit_import_batch(
    db: Session,
    tenant_id: str,
    actor_id: str,
    batch_id: str,
    *,
    expected_version: int,
    review_note: str,
) -> dict:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
            {"scope": f"asset-import:{tenant_id}"},
        )
    query = select(AssetImportBatch).where(
        AssetImportBatch.id == batch_id,
        AssetImportBatch.tenant_id == tenant_id,
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    batch = db.scalar(query)
    if not batch:
        raise AssetImportError("导入批次不存在", "IMPORT_BATCH_NOT_FOUND", 404)
    if batch.status == "committed":
        return _batch_payload(batch, idempotent_replay=True)
    if batch.status != "ready":
        raise AssetImportError("存在校验错误的批次不能提交", "IMPORT_BATCH_BLOCKED")
    if batch.version != expected_version:
        raise AssetImportError("导入批次版本已变化，请刷新后重新确认", "STALE_IMPORT_BATCH")
    if batch.created_by == actor_id:
        raise AssetImportError("预演创建人与提交确认人必须是不同账号", "SELF_APPROVAL_FORBIDDEN", 403)

    case_ids = [str(row["case_id"]) for row in batch.normalized_rows]
    current_cases = set(
        db.scalars(
            select(CaseRecord.case_id).where(
                CaseRecord.tenant_id == tenant_id,
                CaseRecord.case_id.in_(case_ids),
            )
        ).all()
    )
    if current_cases:
        raise AssetImportError(
            f"预演后已有案件被创建：{'、'.join(sorted(current_cases))}",
            "STALE_CASE_CONFLICT",
        )

    package_ids = {str(row["package_id"]) for row in batch.normalized_rows}
    existing_packages = {
        row.package_id: row
        for row in db.scalars(
            select(AssetPackage).where(
                AssetPackage.tenant_id == tenant_id,
                AssetPackage.package_id.in_(package_ids),
            )
        )
    }
    rule_ids = {str(row["commission_rule_id"]) for row in batch.normalized_rows}
    existing_rules = _existing_rules_by_id(db, tenant_id, rule_ids)

    created_packages: set[str] = set()
    created_rules: set[str] = set()
    for row in batch.normalized_rows:
        package = existing_packages.get(row["package_id"])
        if package and package.title != row["package_title"]:
            raise AssetImportError(
                f"资产包 {row['package_id']} 的名称与预演不一致",
                "STALE_PACKAGE_CONFLICT",
            )
        if not package and row["package_id"] not in created_packages:
            db.add(
                AssetPackage(
                    tenant_id=tenant_id,
                    package_id=row["package_id"],
                    title=row["package_title"],
                    policy_status="draft",
                    policy_version=1,
                    budget_limit_yuan=30,
                    source_import_batch_id=batch.id,
                )
            )
            created_packages.add(row["package_id"])

        rule_versions = existing_rules.get(row["commission_rule_id"], [])
        rule = _find_matching_active_rule(rule_versions, row)
        if rule_versions and not rule:
            raise AssetImportError(
                f"佣金规则 {row['commission_rule_id']} 没有与预演一致的启用版本",
                "STALE_COMMISSION_RULE_CONFLICT",
            )
        if not rule_versions and row["commission_rule_id"] not in created_rules:
            db.add(
                CommissionRule(
                    tenant_id=tenant_id,
                    rule_id=row["commission_rule_id"],
                    package_id=row["package_id"],
                    version=1,
                    rate_bps=row["commission_rate_bps"],
                    status="active",
                )
            )
            created_rules.add(row["commission_rule_id"])

        db.add(
            CaseRecord(
                tenant_id=tenant_id,
                case_id=row["case_id"],
                package_id=row["package_id"],
                status=row["case_status"],
                blocked=False,
                has_signed_plan=False,
                contact_basis_ref=row["contact_basis_ref"],
                source_import_batch_id=batch.id,
            )
        )
        db.add(
            CaseFinancialProfile(
                tenant_id=tenant_id,
                case_id=row["case_id"],
                commission_rule_id=row["commission_rule_id"],
                claim_balance_cents=row["claim_balance_cents"],
                mandate_start=date.fromisoformat(row["mandate_start"]),
                mandate_end=date.fromisoformat(row["mandate_end"]),
                signed_plan_at=None,
                signed_plan_last_due=None,
                signed_plan_tail_eligible=False,
            )
        )

    batch.status = "committed"
    batch.version += 1
    batch.committed_by = actor_id
    batch.committed_at = datetime.now(UTC).replace(tzinfo=None)
    batch.review_note = review_note
    db.flush()
    return _batch_payload(batch)
