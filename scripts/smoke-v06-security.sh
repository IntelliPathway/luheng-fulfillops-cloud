#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$(mktemp -d)"
API_PID=""

cleanup() {
  if [[ -n "$API_PID" ]]; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ "$RUN_DIR" == /tmp/* ]]; then
    rm -rf "$RUN_DIR"
  fi
}
trap cleanup EXIT

export DATABASE_URL="sqlite:///$RUN_DIR/security-smoke.db"
export SECRET_STORE_BACKEND="local-envelope"
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v1"
export PYTHONPATH="$PROJECT_ROOT/backend"

cd "$PROJECT_ROOT/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8017 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8017/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done

HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
CURRENT="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8017/api/v1/integrations)"
PAYLOAD="$(jq -c '.services.model | {provider, settings, credential:"smoke-secret-2468"}' <<<"$CURRENT")"
UPDATED="$(curl --silent --fail "${HEADERS[@]}" -X PUT -d "$PAYLOAD" http://127.0.0.1:8017/api/v1/integrations/model)"
HEALTH="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8017/api/v1/security/secrets/health)"

if rg -a --quiet 'smoke-secret-2468' "$RUN_DIR/security-smoke.db"; then
  echo 'plaintext credential found in SQLite file' >&2
  exit 1
fi

RESOLVED="$(python - <<'PY'
import os
from sqlalchemy import select
from app.db import build_engine, build_session_factory
from app.models import ServiceConfig
from app.secret_store import resolve_secret

session_factory = build_session_factory(build_engine(os.environ["DATABASE_URL"]))
with session_factory() as db:
    config = db.scalar(select(ServiceConfig).where(ServiceConfig.tenant_id == "TENANT_A", ServiceConfig.service_type == "model"))
    print(resolve_secret(db, config.secret_ref, "TENANT_A", "model"))
PY
)"
test "$RESOLVED" = "smoke-secret-2468"

jq -n \
  --arg credential_mask "$(jq -r '.services.model.credential_mask' <<<"$UPDATED")" \
  --arg backend "$(jq -r '.backend' <<<"$HEALTH")" \
  --arg status "$(jq -r '.status' <<<"$HEALTH")" \
  --arg key_version "$(jq -r '.key_version' <<<"$HEALTH")" \
  --argjson active_secrets "$(jq -r '.active_secrets' <<<"$HEALTH")" \
  --argjson rotation_pending "$(jq -r '.rotation_pending' <<<"$HEALTH")" \
  '{credential_mask:$credential_mask,backend:$backend,status:$status,key_version:$key_version,active_secrets:$active_secrets,rotation_pending:$rotation_pending,plaintext_in_database:false,runtime_resolution:true}'
