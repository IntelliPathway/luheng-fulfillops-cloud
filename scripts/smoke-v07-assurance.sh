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

export DATABASE_URL="sqlite:///$RUN_DIR/assurance-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export SECRET_STORE_BACKEND=local-envelope
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v1"
export PYTHONPATH="$PROJECT_ROOT/backend"

cd "$PROJECT_ROOT/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8018 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8018/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done

HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
AUTH_HEALTH="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8018/api/v1/security/auth/health)"
QUEUED="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"suite_name":"fulfillops-safe-core","idempotency_key":"v07-smoke-replay"}' http://127.0.0.1:8018/api/v1/agents/replays/jobs)"
JOB_ID="$(jq -r '.id' <<<"$QUEUED")"
JOB="$(curl --silent --fail "${HEADERS[@]}" "http://127.0.0.1:8018/api/v1/jobs/$JOB_ID")"
REPLAY_ID="$(jq -r '.result.replay_run_id' <<<"$JOB")"
REPLAY="$(curl --silent --fail "${HEADERS[@]}" "http://127.0.0.1:8018/api/v1/agents/replays/$REPLAY_ID")"

test "$(jq -r '.mode' <<<"$AUTH_HEALTH")" = "development"
test "$(jq -r '.status' <<<"$JOB")" = "succeeded"
test "$(jq -r '.status' <<<"$REPLAY")" = "passed"
test "$(jq -r '.passed_count' <<<"$REPLAY")" = "4"
test "$(jq -r '.failed_count' <<<"$REPLAY")" = "0"
test "$(jq -r '.results[] | select(.case_id == "forbidden-shell") | .tool_trace[0].status' <<<"$REPLAY")" = "blocked"

jq -n \
  --arg auth_mode "$(jq -r '.mode' <<<"$AUTH_HEALTH")" \
  --arg replay_run_id "$REPLAY_ID" \
  --arg dataset_digest "$(jq -r '.dataset_digest' <<<"$REPLAY")" \
  --argjson passed_count "$(jq -r '.passed_count' <<<"$REPLAY")" \
  '{auth_mode:$auth_mode,replay_run_id:$replay_run_id,dataset_digest:$dataset_digest,passed_count:$passed_count,external_model_calls:0,business_writes:0}'
