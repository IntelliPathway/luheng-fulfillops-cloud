from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import CaseFinancialProfile, CaseRecord


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app("sqlite:///:memory:")) as test_client:
        yield test_client


def headers(tenant: str = "TENANT_A", actor: str = "test-viewer") -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "X-Actor-ID": actor}


def test_package_catalog_is_tenant_scoped_and_uses_ledger_aggregates(client: TestClient) -> None:
    response = client.get("/api/v1/asset-packages?page_size=100", headers=headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["pages"] == 1
    packages = {item["package_id"]: item for item in payload["items"]}
    package_a = packages["PKG_A"]
    assert package_a["data_source"] == "server-authoritative"
    assert package_a["case_count"] == 7
    assert package_a["executable_count"] == 6
    assert package_a["total_claim_balance_cents"] == 9_060_000
    assert package_a["confirmed_net_recovery_cents"] == 1_792_000
    assert package_a["accrued_commission_cents"] == 268_800
    assert package_a["commission_rate_bps"] == 1500

    tenant_b = client.get("/api/v1/asset-packages", headers=headers("TENANT_B")).json()
    assert tenant_b["total"] == 1
    assert tenant_b["items"][0]["package_id"] == "PKG_C"


def test_package_catalog_search_status_and_pagination(client: TestClient) -> None:
    filtered = client.get(
        "/api/v1/asset-packages?query=二期&policy_status=published&page=1&page_size=1",
        headers=headers(),
    ).json()
    assert filtered["total"] == 1
    assert filtered["page_size"] == 1
    assert filtered["items"][0]["package_id"] == "PKG_B"
    assert client.get("/api/v1/asset-packages?query=%25", headers=headers()).json()["total"] == 0


def test_case_catalog_filters_facets_and_paginates_on_the_server(client: TestClient) -> None:
    first = client.get("/api/v1/cases?page=1&page_size=3", headers=headers()).json()
    second = client.get("/api/v1/cases?page=2&page_size=3", headers=headers()).json()
    assert first["total"] == 11
    assert first["pages"] == 4
    assert first["facets"] == {"all": 11, "signed": 5, "blocked": 2, "quality": 0}
    assert [item["case_id"] for item in first["items"]] == ["C001", "C002", "C003"]
    assert [item["case_id"] for item in second["items"]] == ["C004", "C005", "C006"]

    blocked = client.get("/api/v1/cases?view=blocked&package_id=PKG_B", headers=headers()).json()
    assert blocked["total"] == 2
    assert {item["case_id"] for item in blocked["items"]} == {"C010", "C014"}
    assert all(item["protection_reason"] for item in blocked["items"])

    signed = client.get("/api/v1/cases?view=signed&query=C00", headers=headers()).json()
    assert [item["case_id"] for item in signed["items"]] == ["C001", "C002", "C003"]


def test_case_catalog_cursor_is_stable_and_bound_to_filters(client: TestClient) -> None:
    first = client.get("/api/v1/cases?page_size=3", headers=headers()).json()
    assert first["next_cursor"]
    second = client.get(
        f"/api/v1/cases?page_size=3&cursor={first['next_cursor']}",
        headers=headers(),
    ).json()
    assert [item["case_id"] for item in second["items"]] == ["C004", "C005", "C006"]
    assert not ({item["case_id"] for item in first["items"]} & {item["case_id"] for item in second["items"]})

    mismatched = client.get(
        f"/api/v1/cases?page_size=3&view=blocked&cursor={first['next_cursor']}",
        headers=headers(),
    )
    assert mismatched.status_code == 422
    assert "CURSOR_SCOPE_MISMATCH" in mismatched.text


def test_case_catalog_balance_sort_and_detail_are_authoritative(client: TestClient) -> None:
    sorted_cases = client.get("/api/v1/cases?sort=balance_desc&page_size=2", headers=headers()).json()
    assert [item["case_id"] for item in sorted_cases["items"]] == ["C016", "C015"]

    detail = client.get("/api/v1/cases/C001", headers=headers()).json()
    assert detail["data_source"] == "server-authoritative"
    assert detail["claim_balance_cents"] == 1_200_000
    assert detail["confirmed_net_recovery_cents"] == 1_000_000
    assert detail["accrued_commission_cents"] == 150_000
    assert detail["active_plan_id"] == "PLAN001"
    assert detail["next_allowed"] == "仅保留账务与审计记录"
    assert "debtor" not in detail
    assert "phone" not in detail


def test_case_detail_does_not_cross_tenant_boundary(client: TestClient) -> None:
    denied = client.get("/api/v1/cases/C001", headers=headers("TENANT_B"))
    assert denied.status_code == 404
    assert "CASE_NOT_FOUND" in denied.text


def test_low_completeness_view_is_computed_from_server_records(client: TestClient) -> None:
    with client.app.state.Session() as db:
        db.add(
            CaseRecord(
                tenant_id="TENANT_A",
                case_id="C-INCOMPLETE",
                package_id="PKG_A",
                status="资料待补",
            )
        )
        db.commit()

    payload = client.get("/api/v1/cases?view=quality", headers=headers()).json()
    assert payload["total"] == 1
    assert payload["facets"]["quality"] == 1
    assert payload["items"][0]["case_id"] == "C-INCOMPLETE"
    assert payload["items"][0]["data_completeness_score"] == 20
    assert payload["items"][0]["claim_balance_cents"] is None


def test_future_mandate_is_not_counted_as_executable(client: TestClient) -> None:
    with client.app.state.Session() as db:
        profile = db.scalar(
            select(CaseFinancialProfile).where(
                CaseFinancialProfile.tenant_id == "TENANT_A",
                CaseFinancialProfile.case_id == "C004",
            )
        )
        assert profile is not None
        profile.mandate_start = date.today() + timedelta(days=1)
        profile.mandate_end = profile.mandate_start + timedelta(days=30)
        db.commit()

    packages = client.get("/api/v1/asset-packages?page_size=100", headers=headers()).json()["items"]
    package_a = next(item for item in packages if item["package_id"] == "PKG_A")
    detail = client.get("/api/v1/cases/C004", headers=headers()).json()
    assert package_a["executable_count"] == 5
    assert detail["next_allowed"] == "委托尚未开始，等待生效后重新预检"


def test_committed_import_is_immediately_visible_in_catalog(client: TestClient) -> None:
    csv_text = "\n".join(
        [
            "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref",
            "PKG-CATALOG,目录验收资产包,C-CATALOG,3200000,2026-09-01,2027-08-31,COM-CATALOG-V1,1700,CONSENT-C-CATALOG",
        ]
    )
    preview = client.post(
        "/api/v1/asset-imports/previews",
        headers=headers(actor="test-user"),
        json={"filename": "catalog.csv", "csv_text": csv_text, "idempotency_key": "catalog-import-001"},
    ).json()
    committed = client.post(
        f"/api/v1/asset-imports/{preview['id']}/commit",
        headers=headers(actor="Terry"),
        json={
            "expected_version": preview["version"],
            "review_note": "已独立核对目录导入字段与金额",
            "acknowledged": True,
        },
    )
    assert committed.status_code == 200

    package = client.get("/api/v1/asset-packages?query=PKG-CATALOG", headers=headers()).json()["items"][0]
    case = client.get("/api/v1/cases/C-CATALOG", headers=headers()).json()
    assert package["policy_status"] == "draft"
    assert package["case_count"] == 1
    assert package["executable_count"] == 0
    assert case["data_completeness_score"] == 100
    assert case["source_import_batch_id"] == preview["id"]
    assert case["next_allowed"] == "资产包策略发布后重新预检"
