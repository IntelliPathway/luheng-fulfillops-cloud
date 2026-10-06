from fastapi.testclient import TestClient

from app.main import create_app


def test_case_validation_never_calls_seed_data_real_and_is_tenant_scoped():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        headers = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-viewer"}
        response = client.get("/api/v1/pilot/case-validation?case_id=C002", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "evidence_incomplete"
        assert not data["real_business_verified"]
        assert not data["enables_external_execution"]
        assert data["source_digest"] is None
        assert len(data["report_digest"]) == 64
        assert not next(c for c in data["checks"] if c["id"] == "source")["passed"]
        assert not next(c for c in data["checks"] if c["id"] == "environment")["passed"]
        again = client.get("/api/v1/pilot/case-validation?case_id=C002", headers=headers).json()
        assert again["report_digest"] == data["report_digest"]
        assert (
            client.get(
                "/api/v1/pilot/case-validation?case_id=C002", headers={**headers, "X-Tenant-ID": "TENANT_B"}
            ).status_code
            == 404
        )
        assert client.get("/api/v1/pilot/case-validation?case_id=C002").status_code == 401
        assert client.get("/api/v1/pilot/case-validation?case_id=missing", headers=headers).status_code == 404


def test_imported_case_keeps_source_digest_and_requires_policy_and_real_outcome():
    with TestClient(create_app("sqlite:///:memory:")) as client:
        actor = {"X-Tenant-ID": "TENANT_A", "X-Actor-ID": "test-user"}
        csv = "package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status\nPKG_VALIDATE,测试脱敏资产,VERIFY001,1200000,2026-01-01,2027-12-31,COM_VALIDATE,1500,CONSENT-VERIFY001,待联系"
        created = client.post(
            "/api/v1/asset-imports/previews",
            headers=actor,
            json={"filename": "cases.csv", "csv_text": csv, "idempotency_key": "validate-import-case"},
        )
        assert created.status_code == 201, created.text
        batch = created.json()
        reviewed = client.post(
            f"/api/v1/asset-imports/{batch['id']}/commit",
            headers={**actor, "X-Actor-ID": "Terry"},
            json={"expected_version": 1, "review_note": "独立核对脱敏案件的金额和委托期限", "acknowledged": True},
        )
        assert reviewed.status_code == 200, reviewed.text
        report = client.get("/api/v1/pilot/case-validation?case_id=VERIFY001", headers=actor).json()
        checks = {c["id"]: c for c in report["checks"]}
        assert checks["source"]["passed"]
        assert checks["mandate"]["passed"]
        assert not checks["policy"]["passed"]
        assert not checks["outcome"]["passed"]
        assert report["source_digest"] == batch["source_digest"]
        assert report["confirmed_net_recovery_cents"] == 0
        assert report["ledger"] == []
        assert not report["real_business_verified"]
        assert "source_filename" not in report
