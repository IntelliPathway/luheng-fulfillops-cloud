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

export DATABASE_URL="sqlite:///$RUN_DIR/worker-smoke.db"
export JOB_EXECUTION_MODE="external"
export WORKER_ID="smoke-worker-1"
export PYTHONPATH="$PROJECT_ROOT/backend"

cd "$PROJECT_ROOT/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8015/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done
curl --silent --fail http://127.0.0.1:8015/api/v1/health >/dev/null

QUEUED="$(curl --silent --fail \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-ID: TENANT_A' \
  -H 'X-Actor-ID: test-user' \
  -d '{"idempotency_key":"worker-cli-smoke-v050"}' \
  http://127.0.0.1:8015/api/v1/integrations/voice/connection-test/jobs)"
JOB_ID="$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$QUEUED")"
INITIAL_STATUS="$(python -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<<"$QUEUED")"

python -m app.worker --once

COMPLETED="$(curl --silent --fail \
  -H 'X-Tenant-ID: TENANT_A' \
  -H 'X-Actor-ID: test-user' \
  "http://127.0.0.1:8015/api/v1/jobs/$JOB_ID")"

python -c '
import json, sys
job = json.load(sys.stdin)
assert job["status"] == "succeeded", job
assert job["attempt"] == 1, job
assert job["lease_owner"] is None, job
print(json.dumps({
    "job_id": job["id"],
    "initial_status": sys.argv[1],
    "final_status": job["status"],
    "attempt": job["attempt"],
    "recovery_count": job["recovery_count"],
    "lease_cleared": job["lease_owner"] is None,
}, ensure_ascii=False, indent=2))
' "$INITIAL_STATUS" <<<"$COMPLETED"
