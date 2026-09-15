#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/backend/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到后端 Python 环境：$PYTHON_BIN" >&2
  echo "请先按 README 安装 backend/requirements.txt，或通过 PYTHON_BIN 指定解释器。" >&2
  exit 2
fi

"$PYTHON_BIN" -m ruff check "$PROJECT_ROOT/backend/app" "$PROJECT_ROOT/backend/tests"
"$PYTHON_BIN" -m ruff format --check "$PROJECT_ROOT/backend/app" "$PROJECT_ROOT/backend/tests"
(
  cd "$PROJECT_ROOT/backend"
  "$PYTHON_BIN" -m pytest -q
)
(
  cd "$PROJECT_ROOT"
  npm test
)
