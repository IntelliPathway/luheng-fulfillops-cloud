from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.main import create_app
from app.migrations import run_sqlite_compatibility_migrations
from app.models import (
    AssetImportBatch,
    AssetPackage,
    AuditEvent,
    CaseFinancialProfile,
    CaseRecord,
    CommissionRule,
)

CSV_HEADER = (
    "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,"
    "commission_rule_id,commission_rate_bps,contact_basis_ref,case_status"
)


@pytest.fixture()
def client() -> TestClient:
    app = create_app("sqlite:///:memory:")
    with TestClient(app) as test_client:
        yield test_client


def headers(actor: str = "test-operator", tenant: str = "TENANT_A") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def csv_text(case_id: str = "C901", package_id: str = "PKG_NEW") -> str:
    return "\n".join(
        [
            CSV_HEADER,
            f"{package_id},测试资产包,{case_id},1200000,2026-09-01,2027-08-31,COM_NEW_V1,1500,CONSENT-{case_id},待联系",
        ]
    )


def preview(
    client: TestClient,
    *,
    body: str | None = None,
    key: str = "asset-import-test-001",
    actor: str = "test-operator",
    tenant: str = "TENANT_A",
):
    return client.post(
        "/api/v1/asset-imports/previews",
        headers=headers(actor, tenant),
        json={"filename": "asset_cases.csv", "csv_text": body or csv_text(), "idempotency_key": key},
    )


def test_preview_and_independent_commit_are_atomic_and_audited(client: TestClient) -> None:
    created = preview(client, actor="test-user")
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["status"] == "ready"
    assert batch["valid_count"] == 1
    assert batch["package_count"] == 1
    assert batch["total_claim_balance_cents"] == 1_200_000
    assert batch["source_digest"] not in csv_text()

    self_review = client.post(
        f"/api/v1/asset-imports/{batch['id']}/commit",
        headers=headers("test-user"),
        json={"expected_version": 1, "review_note": "本人创建的批次不能自行确认提交", "acknowledged": True},
    )
    assert self_review.status_code == 403
    assert "SELF_APPROVAL_FORBIDDEN" in self_review.text

    committed = client.post(
        f"/api/v1/asset-imports/{batch['id']}/commit",
        headers=headers("Terry"),
        json={"expected_version": 1, "review_note": "已独立复核字段金额与委托期限一致", "acknowledged": True},
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["status"] == "committed"
    assert result["version"] == 2
    assert result["committed_by"] == "Terry"
    assert result["review_note"] == "已独立复核字段金额与委托期限一致"

    session_factory = client.app.state.Session
    with session_factory() as db:
        package = db.scalar(
            select(AssetPackage).where(AssetPackage.tenant_id == "TENANT_A", AssetPackage.package_id == "PKG_NEW")
        )
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == "TENANT_A", CaseRecord.case_id == "C901"))
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == "TENANT_A", CaseFinancialProfile.case_id == "C901"
            )
        )
        rule = db.scalar(
            select(CommissionRule).where(CommissionRule.tenant_id == "TENANT_A", CommissionRule.rule_id == "COM_NEW_V1")
        )
        audit_actions = set(
            db.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.tenant_id == "TENANT_A", AuditEvent.resource_id == batch["id"]
                )
            ).all()
        )
        stored_batch = db.get(AssetImportBatch, batch["id"])
        assert package and package.policy_status == "draft"
        assert package.source_import_batch_id == batch["id"]
        assert case and case.contact_basis_ref == "CONSENT-C901"
        assert case.source_import_batch_id == batch["id"]
        assert profile and profile.claim_balance_cents == 1_200_000
        assert rule and rule.rate_bps == 1500
        assert audit_actions == {"asset_import.previewed", "asset_import.committed"}
        assert stored_batch and csv_text() not in str(stored_batch.normalized_rows)


def test_preview_rejects_pii_headers_and_does_not_commit(client: TestClient) -> None:
    body = f"{CSV_HEADER},phone\nPKG_NEW,测试资产包,C902,1200000,2026-09-01,2027-08-31,COM_NEW_V1,1500,CONSENT-C902,待联系,13800000000"
    response = preview(client, body=body, key="asset-import-pii-001")
    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "blocked"
    assert batch["valid_count"] == 0
    assert {issue["code"] for issue in batch["issues"]} >= {"PII_FIELD_FORBIDDEN"}
    denied = client.post(
        f"/api/v1/asset-imports/{batch['id']}/commit",
        headers=headers("test-user"),
        json={"expected_version": 1, "review_note": "批次存在错误时不能提交入库", "acknowledged": True},
    )
    assert denied.status_code == 409


def test_preview_reports_row_errors_without_partial_commit(client: TestClient) -> None:
    body = "\n".join(
        [
            CSV_HEADER,
            "PKG_NEW,测试资产包,C903,1200000,2026-09-01,2027-08-31,COM_NEW_V1,1500,CONSENT-C903,待联系",
            "PKG_NEW,测试资产包,C904,not-money,2027-09-01,2027-08-31,COM_NEW_V1,1500,CONSENT-C904,已结清",
        ]
    )
    response = preview(client, body=body, key="asset-import-invalid-row")
    batch = response.json()
    assert batch["status"] == "blocked"
    assert batch["row_count"] == 2
    assert batch["valid_count"] == 1
    assert batch["invalid_count"] == 1
    assert {issue["code"] for issue in batch["issues"]} >= {
        "INVALID_INTEGER",
        "INVALID_MANDATE_RANGE",
        "UNSAFE_INITIAL_STATUS",
    }
    with client.app.state.Session() as db:
        assert db.scalar(select(CaseRecord).where(CaseRecord.case_id.in_(["C903", "C904"]))) is None


def test_idempotency_replay_and_payload_conflict(client: TestClient) -> None:
    first = preview(client, key="asset-import-idempotent")
    repeated = preview(client, key="asset-import-idempotent")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["idempotent_replay"] is True
    conflict = preview(
        client,
        body=csv_text(case_id="C905"),
        key="asset-import-idempotent",
    )
    assert conflict.status_code == 409
    assert "IDEMPOTENCY_CONFLICT" in conflict.text


def test_existing_case_is_skipped_without_overwrite_and_ids_are_tenant_scoped(client: TestClient) -> None:
    duplicate = preview(client, body=csv_text(case_id="C001", package_id="PKG_A"), key="existing-case")
    duplicate_batch = duplicate.json()
    assert duplicate_batch["status"] == "blocked"
    assert duplicate_batch["duplicate_count"] == 1
    assert duplicate_batch["valid_count"] == 0
    assert duplicate_batch["issues"][0]["severity"] == "warning"

    tenant_b = preview(
        client,
        body=csv_text(case_id="C001", package_id="PKG_NEW_B"),
        key="cross-tenant-case",
        tenant="TENANT_B",
    )
    assert tenant_b.json()["status"] == "ready"
    committed = client.post(
        f"/api/v1/asset-imports/{tenant_b.json()['id']}/commit",
        headers=headers("test-user", "TENANT_B"),
        json={"expected_version": 1, "review_note": "已独立复核租户乙导入数据一致", "acknowledged": True},
    )
    assert committed.status_code == 200, committed.text


def test_preview_blocks_existing_package_or_rule_conflicts(client: TestClient) -> None:
    package_conflict = preview(
        client,
        body=csv_text(case_id="C906", package_id="PKG_A"),
        key="package-title-conflict",
    ).json()
    assert package_conflict["status"] == "blocked"
    assert package_conflict["issues"][0]["code"] == "EXISTING_PACKAGE_CONFLICT"

    rule_conflict_body = csv_text(case_id="C907").replace("COM_NEW_V1,1500", "COM_A_V1,1600")
    rule_conflict = preview(client, body=rule_conflict_body, key="commission-rule-conflict").json()
    assert rule_conflict["status"] == "blocked"
    assert rule_conflict["issues"][0]["code"] == "EXISTING_COMMISSION_RULE_CONFLICT"

    with client.app.state.Session() as db:
        db.add(
            CommissionRule(
                tenant_id="TENANT_A",
                rule_id="COM_ARCHIVED_V1",
                package_id="PKG_NEW",
                version=1,
                rate_bps=1500,
                status="inactive",
            )
        )
        db.commit()
    archived_rule_body = csv_text(case_id="C908").replace("COM_NEW_V1", "COM_ARCHIVED_V1")
    archived_rule = preview(client, body=archived_rule_body, key="archived-rule-conflict").json()
    assert archived_rule["status"] == "blocked"
    assert archived_rule["issues"][0]["code"] == "EXISTING_COMMISSION_RULE_CONFLICT"


def test_commit_fails_closed_when_preview_becomes_stale(client: TestClient) -> None:
    first = preview(client, key="stale-preview-a").json()
    second = preview(client, key="stale-preview-b").json()
    committed = client.post(
        f"/api/v1/asset-imports/{first['id']}/commit",
        headers=headers("test-user"),
        json={"expected_version": 1, "review_note": "已独立复核第一批次数据一致", "acknowledged": True},
    )
    assert committed.status_code == 200
    stale = client.post(
        f"/api/v1/asset-imports/{second['id']}/commit",
        headers=headers("Terry"),
        json={"expected_version": 1, "review_note": "尝试提交已陈旧的第二批次", "acknowledged": True},
    )
    assert stale.status_code == 409
    assert "STALE_CASE_CONFLICT" in stale.text


def test_import_access_control_and_tenant_listing(client: TestClient) -> None:
    assert preview(client, actor="test-viewer").status_code == 403
    created = preview(client, key="tenant-listing").json()
    tenant_a = client.get("/api/v1/asset-imports", headers=headers("test-viewer")).json()
    tenant_b = client.get("/api/v1/asset-imports", headers=headers("test-viewer", "TENANT_B")).json()
    assert tenant_a[0]["id"] == created["id"]
    assert tenant_b == []


def test_sqlite_compatibility_adds_import_metadata_columns() -> None:
    app = create_app("sqlite:///:memory:")
    with app.state.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE asset_import_batches")
        connection.exec_driver_sql("DROP TABLE cases")
        connection.exec_driver_sql("DROP TABLE asset_packages")
        connection.exec_driver_sql(
            "CREATE TABLE asset_packages (id varchar(40) PRIMARY KEY, tenant_id varchar(40), package_id varchar(40), title varchar(120))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE cases (id varchar(40) PRIMARY KEY, tenant_id varchar(40), case_id varchar(40), package_id varchar(40), status varchar(40))"
        )
    applied = run_sqlite_compatibility_migrations(app.state.engine)
    inspector = inspect(app.state.engine)
    assert {"source_import_batch_id", "created_at"} <= {
        column["name"] for column in inspector.get_columns("asset_packages")
    }
    assert {"contact_basis_ref", "source_import_batch_id", "version", "created_at"} <= {
        column["name"] for column in inspector.get_columns("cases")
    }
    assert {"source_import_batch_id", "contact_basis_ref", "version", "created_at"} <= set(applied)
