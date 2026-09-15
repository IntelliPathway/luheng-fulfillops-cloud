from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, Tenant, TenantMembership, User

VALID_ROLES = {"viewer", "operator", "admin"}


class ProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdentityProvisioningRequest:
    tenant_id: str
    tenant_name: str
    user_id: str
    email: str
    display_name: str
    role: str = "admin"


def provision_identity(db: Session, request: IdentityProvisioningRequest) -> TenantMembership:
    """Create the first tenant membership idempotently without demo business data."""

    if request.role not in VALID_ROLES:
        raise ProvisioningError("role 必须是 viewer、operator 或 admin")
    if not all(
        value.strip()
        for value in (request.tenant_id, request.tenant_name, request.user_id, request.email, request.display_name)
    ):
        raise ProvisioningError("租户和用户字段不能为空")

    tenant = db.get(Tenant, request.tenant_id)
    if tenant and tenant.name != request.tenant_name:
        raise ProvisioningError("租户已存在，但名称与本次输入不一致")
    if not tenant:
        tenant = Tenant(id=request.tenant_id, name=request.tenant_name)
        db.add(tenant)

    user = db.get(User, request.user_id)
    email_owner = db.scalar(select(User).where(User.email == request.email))
    if email_owner and email_owner.id != request.user_id:
        raise ProvisioningError("邮箱已属于其他用户")
    if user and (user.email != request.email or user.display_name != request.display_name):
        raise ProvisioningError("用户已存在，但邮箱或显示名与本次输入不一致")
    if not user:
        user = User(id=request.user_id, email=request.email, display_name=request.display_name)
        db.add(user)

    db.flush()
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == request.tenant_id,
            TenantMembership.user_id == request.user_id,
        )
    )
    if membership and membership.role != request.role:
        raise ProvisioningError("成员关系已存在，但角色与本次输入不一致")
    if not membership:
        membership = TenantMembership(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            role=request.role,
        )
        db.add(membership)
        db.flush()
        db.add(
            AuditEvent(
                tenant_id=request.tenant_id,
                actor_id=f"bootstrap:{request.user_id}",
                action="identity.bootstrap",
                resource_type="tenant_membership",
                resource_id=membership.id,
                detail={"role": request.role},
            )
        )
    db.commit()
    db.refresh(membership)
    return membership
