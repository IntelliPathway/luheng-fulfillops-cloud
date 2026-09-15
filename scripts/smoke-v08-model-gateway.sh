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

export DATABASE_URL="sqlite:///$RUN_DIR/model-gateway-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export SECRET_STORE_BACKEND=local-envelope
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v1"
export ENABLE_LIVE_MODEL_CALLS=false
export MODEL_EGRESS_ALLOWLIST=api.deepseek.com
export PYTHONPATH="$PROJECT_ROOT/backend"

if [[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
else
  PYTHON_BIN=python
fi

cd "$PROJECT_ROOT/backend"
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8019 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8019/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done

HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
HEALTH="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8019/api/v1/models/gateway/health)"
QUEUED="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"mode":"deterministic-contract","idempotency_key":"v08-smoke-replay"}' http://127.0.0.1:8019/api/v1/agents/replays/jobs)"
JOB_ID="$(jq -r '.id' <<<"$QUEUED")"
JOB="$QUEUED"
for _ in $(seq 1 40); do
  JOB="$(curl --silent --fail "${HEADERS[@]}" "http://127.0.0.1:8019/api/v1/jobs/$JOB_ID")"
  if [[ "$(jq -r '.status' <<<"$JOB")" =~ ^(succeeded|failed|cancelled)$ ]]; then break; fi
  sleep 0.1
done
LIVE_STATUS="$(curl --silent -o "$RUN_DIR/live.json" -w '%{http_code}' "${HEADERS[@]}" -X POST -d '{"mode":"live-provider","acknowledged_external_call":true}' http://127.0.0.1:8019/api/v1/agents/replays/jobs)"

test "$(jq -r '.status' <<<"$HEALTH")" = "contract"
test "$(jq -r '.live_calls_enabled' <<<"$HEALTH")" = "false"
test "$(jq -r '.status' <<<"$JOB")" = "succeeded"
test "$(jq -r '.result.external_call_count' <<<"$JOB")" = "0"
test "$LIVE_STATUS" = "409"

jq -n \
  --arg model "$(jq -r '.model' <<<"$HEALTH")" \
  --arg endpoint_host "$(jq -r '.endpoint_host' <<<"$HEALTH")" \
  --arg gateway_status "$(jq -r '.status' <<<"$HEALTH")" \
  --arg replay_status "$(jq -r '.result.status' <<<"$JOB")" \
  --arg live_status "$LIVE_STATUS" \
  '{model:$model,endpoint_host:$endpoint_host,gateway_status:$gateway_status,replay_status:$replay_status,live_request_http_status:$live_status,external_model_calls:0}'
