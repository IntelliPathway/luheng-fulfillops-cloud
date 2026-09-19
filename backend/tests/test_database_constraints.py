import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.models import AssetPackage


def test_database_rejects_invalid_policy_values_and_declares_tenant_package_link() -> None:
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
    foreign_keys = inspect(app.state.engine).get_foreign_keys("cases")
    assert any(key["constrained_columns"] == ["tenant_id", "package_id"] for key in foreign_keys)
