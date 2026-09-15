from __future__ import annotations

import argparse

from .config import StartupSettings
from .db import build_engine, build_session_factory
from .migrations import run_postgres_migrations
from .provision import IdentityProvisioningRequest, provision_identity
from .version import PRODUCT_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description=f"幂等创建{PRODUCT_NAME}的首个租户成员，不导入演示业务数据。")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--user-id", required=True, help="必须与 OIDC sub 完全一致")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=["viewer", "operator", "admin"], default="admin")
    args = parser.parse_args()

    settings = StartupSettings.from_environment(seed_demo_data=False)
    engine = build_engine(settings.database_url)
    run_postgres_migrations(engine)
    session_factory = build_session_factory(engine)
    with session_factory() as db:
        membership = provision_identity(
            db,
            IdentityProvisioningRequest(
                tenant_id=args.tenant_id,
                tenant_name=args.tenant_name,
                user_id=args.user_id,
                email=args.email,
                display_name=args.display_name,
                role=args.role,
            ),
        )
    print(f"已确认成员关系 {membership.id}: {membership.tenant_id}/{membership.user_id} ({membership.role})")


if __name__ == "__main__":
    main()
