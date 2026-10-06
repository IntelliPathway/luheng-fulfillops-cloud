from __future__ import annotations

import argparse
import json

from .config import StartupSettings
from .db import build_engine, build_session_factory
from .pilot_preflight import build_pilot_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="只读生产试点预检；不建表、不播种、不执行外部调用")
    parser.add_argument("--tenant-id", required=True)
    args = parser.parse_args()
    try:
        settings = StartupSettings.from_environment()
        engine = build_engine(settings.database_url)
        with build_session_factory(engine)() as db:
            result = build_pilot_preflight(db, args.tenant_id, settings)
        print(json.dumps(result, default=str, ensure_ascii=False))
        return 0 if result["status"] == "ready" else 1
    except Exception:
        print(json.dumps({"status": "blocked", "detail": "配置、数据库连接或版本结构预检失败"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
