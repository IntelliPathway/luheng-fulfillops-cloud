import pytest
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.models import AssetPackage, CaseRecord


def test_database_rejects_invalid_policy_values_and_cross_tenant_package_links() -> None:
    app = create_app("sqlite:///:memory:")
    with app.state.Session() as db:
        db.add(
            AssetPackage(
                tenant_id="TENANT_A",
                package_id="INVALID",
                title="invalid",
                policy_status="draft",
                policy_version=1,
                budget_limit_yuan=-1,
                min_settlement_bps=7000,
                max_installments=6,
                min_down_payment_bps=2000,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(CaseRecord(tenant_id="TENANT_B", case_id="CROSS-TENANT", package_id="PKG_A", status="待联系"))
        with pytest.raises(IntegrityError):
            db.commit()
