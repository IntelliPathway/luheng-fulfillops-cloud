from __future__ import annotations

import argparse
import json
import os

from .db import build_engine, build_session_factory
from .secret_store import rewrap_active_secrets, secret_store_status


def main() -> int:
    parser = argparse.ArgumentParser(description="履衡 AI 密钥信封运维")
    parser.add_argument("command", choices=("status", "rotate"))
    parser.add_argument("--tenant", help="只检查或重包裹一个租户")
    args = parser.parse_args()
    session_factory = build_session_factory(build_engine(os.getenv("DATABASE_URL", "sqlite:///./luheng-dev.db")))
    with session_factory() as db:
        if args.command == "rotate":
            count = rewrap_active_secrets(db, args.tenant)
            db.commit()
            print(json.dumps({"rotated": count, "tenant": args.tenant}, ensure_ascii=False))
            return 0
        if not args.tenant:
            parser.error("status 需要 --tenant")
        print(json.dumps(secret_store_status(db, args.tenant).__dict__, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
