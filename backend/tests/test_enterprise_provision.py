import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.enterprise_provision import EnterpriseManifest, provision_enterprise
from app.models import AuditEvent, CaseRecord, Tenant, TenantMembership, TenantPlan, User
from app.provision import ProvisioningError


def manifest(tenant="FIRST", subjects=("first-admin", "first-reviewer")):
    return EnterpriseManifest(
        tenant_id=tenant,
        tenant_name=tenant,
        members=[{"subject": s, "email": s + "@example.com", "display_name": s} for s in subjects],
    )


def test_two_enterprises_are_repeatable_and_have_separate_memberships():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        a = provision_enterprise(db, manifest())
        assert provision_enterprise(db, manifest()) == a
        provision_enterprise(db, manifest("SECOND", ("second-admin", "second-reviewer")))
        assert db.scalar(select(func.count(Tenant.id))) == 2
        assert db.scalar(select(func.count(TenantMembership.id))) == 4
        assert db.scalar(select(func.count(AuditEvent.id))) == 4
        assert db.scalar(select(func.count(TenantPlan.tenant_id))) == 2
        assert db.scalar(select(func.count(CaseRecord.id))) == 0
        assert {
            m.user_id for m in db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == "FIRST"))
        } == {"first-admin", "first-reviewer"}


def test_late_conflict_rolls_back_new_tenant_and_first_member():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        provision_enterprise(db, manifest())
        conflicting = manifest("SECOND", ("new-admin", "first-reviewer"))
        conflicting.members[1].display_name = "different"
        with pytest.raises(ProvisioningError):
            provision_enterprise(db, conflicting)
        assert db.get(Tenant, "SECOND") is None
        assert db.get(User, "new-admin") is None
        assert db.scalar(select(func.count(AuditEvent.id))) == 2


def test_bootstrap_cannot_restore_revoked_membership():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        provision_enterprise(db, manifest())
        row = db.scalar(select(TenantMembership))
        row.status = "revoked"
        db.commit()
        with pytest.raises(ProvisioningError, match="撤销"):
            provision_enterprise(db, manifest())
        assert db.get(TenantMembership, row.id).status == "revoked"


def test_independent_admins_and_unique_subjects_required():
    data = manifest().model_dump()
    data["members"][1]["role"] = "operator"
    with pytest.raises(ValidationError, match="独立管理员"):
        EnterpriseManifest.model_validate(data)
    data = manifest().model_dump()
    data["members"][1]["subject"] = data["members"][0]["subject"]
    with pytest.raises(ValidationError, match="subject"):
        EnterpriseManifest.model_validate(data)


def test_disabled_user_cannot_be_bootstrapped_and_placeholder_is_rejected():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        provision_enterprise(db, manifest())
        db.get(User, "first-admin").status = "disabled"
        db.commit()
        with pytest.raises(ProvisioningError):
            provision_enterprise(db, manifest())
    data = manifest().model_dump()
    data["members"][0]["subject"] = "REPLACE_WITH_SUB"
    with pytest.raises(ValidationError):
        EnterpriseManifest.model_validate(data)
