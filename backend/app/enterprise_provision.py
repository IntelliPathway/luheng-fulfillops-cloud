"""Atomic, repeatable enterprise bootstrap. Does not import business evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from .entitlements import PLAN_CATALOG
from .models import TenantPlan, User
from .provision import IdentityProvisioningRequest, ProvisioningError, provision_identity


class EnterpriseMember(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=80, pattern=r"^\S+$")
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    display_name: str = Field(min_length=1, max_length=120)
    role: str = "admin"

    @model_validator(mode="after")
    def valid_role(self):
        if self.role not in {"viewer", "operator", "admin"}:
            raise ValueError("成员角色无效")
        if self.subject.upper().startswith(("REPLACE_", "CHANGE_ME")):
            raise ValueError("必须使用实际 OIDC subject")
        return self


class EnterpriseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: int = 1
    tenant_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    tenant_name: str = Field(min_length=1, max_length=120)
    plan_code: str = "team"
    members: list[EnterpriseMember] = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def validate_members(self):
        if self.schema_version != 1 or self.plan_code not in PLAN_CATALOG:
            raise ValueError("配置版本或套餐无效")
        if len({m.subject for m in self.members}) != len(self.members):
            raise ValueError("OIDC subject 不得重复")
        if len({m.email.lower() for m in self.members}) != len(self.members):
            raise ValueError("邮箱不得重复")
        if sum(m.role == "admin" for m in self.members) < 2:
            raise ValueError("必须配置两位独立管理员")
        if len(self.members) > PLAN_CATALOG[self.plan_code]["seat_limit"]:
            raise ValueError("成员超过套餐席位")
        return self


def provision_enterprise(db: Session, manifest: EnterpriseManifest) -> dict:
    """All memberships and plan commit together; conflicts roll back the whole operation."""
    try:
        existing = db.get(TenantPlan, manifest.tenant_id)
        if existing and (existing.plan_code != manifest.plan_code or existing.status != "active"):
            raise ProvisioningError("套餐已存在且与初始化配置不一致；请使用套餐治理流程")
        memberships = []
        for member in manifest.members:
            row = provision_identity(
                db,
                IdentityProvisioningRequest(
                    tenant_id=manifest.tenant_id,
                    tenant_name=manifest.tenant_name,
                    user_id=member.subject,
                    email=member.email,
                    display_name=member.display_name,
                    role=member.role,
                ),
                commit=False,
            )
            if row.status != "active" or db.get(User, member.subject).status != "active":
                raise ProvisioningError("成员已撤销；初始化不得恢复成员权限")
            memberships.append(row.id)
        if not existing:
            db.add(
                TenantPlan(
                    tenant_id=manifest.tenant_id,
                    plan_code=manifest.plan_code,
                    status="active",
                    version=1,
                    updated_by="bootstrap:enterprise",
                    **PLAN_CATALOG[manifest.plan_code],
                )
            )
        db.commit()
        return {
            "schema_version": 1,
            "tenant_id": manifest.tenant_id,
            "membership_ids": memberships,
            "business_data_created": False,
            "enables_external_execution": False,
        }
    except Exception:
        db.rollback()
        raise
