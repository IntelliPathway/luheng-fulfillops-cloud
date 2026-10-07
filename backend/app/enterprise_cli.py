from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import StartupSettings
from .db import build_engine, build_session_factory
from .enterprise_provision import EnterpriseManifest, provision_enterprise
from .migrations import run_postgres_migrations


def main():
    parser = argparse.ArgumentParser(description="按标准配置开通企业与两位独立管理员；不创建业务数据")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    manifest = EnterpriseManifest.model_validate_json(Path(args.manifest).read_text(encoding="utf-8"))
    if args.validate_only:
        print(json.dumps({"valid": True, "tenant_id": manifest.tenant_id, "member_count": len(manifest.members)}))
        return
    settings = StartupSettings.from_environment(seed_demo_data=False)
    engine = build_engine(settings.database_url)
    try:
        run_postgres_migrations(engine)
        with build_session_factory(engine)() as db:
            print(json.dumps(provision_enterprise(db, manifest), ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
