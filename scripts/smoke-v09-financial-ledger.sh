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

export DATABASE_URL="sqlite:///$RUN_DIR/financial-ledger-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export SECRET_STORE_BACKEND=local-envelope
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v09"
export PAYMENT_SANDBOX_SECRET="smoke-payment-secret-never-persist-plaintext"
export ENABLE_PAYMENT_SANDBOX=true
export PAYMENT_WEBHOOK_TOLERANCE_SECONDS=300
export PYTHONPATH="$PROJECT_ROOT/backend"

if [[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
else
  PYTHON_BIN="python"
fi

cd "$PROJECT_ROOT/backend"
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8020 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8020/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done

HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
BASE="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8020/api/v1/payments/overview)"
FIRST="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"case_id":"C002","amount_cents":101600,"idempotency_key":"v09-smoke-payment"}' http://127.0.0.1:8020/api/v1/payments/sandbox-receipts)"
DUPLICATE="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"case_id":"C002","amount_cents":101600,"idempotency_key":"v09-smoke-payment"}' http://127.0.0.1:8020/api/v1/payments/sandbox-receipts)"
AFTER_PAYMENT="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8020/api/v1/payments/overview)"

BAD_WEBHOOK_BODY='{"event_id":"SMOKE-BAD-SIGNATURE","event_type":"payment","amount_cents":100,"currency":"CNY","occurred_at":"2026-09-12T12:00:00Z","case_id":"C002"}'
BAD_WEBHOOK_STATUS="$(curl --silent -o "$RUN_DIR/bad-webhook.json" -w '%{http_code}' -H 'Content-Type: application/json' -H "X-FulfillOps-Timestamp: $(date +%s)" -H 'X-FulfillOps-Signature: v1=0000000000000000000000000000000000000000000000000000000000000000' -X POST -d "$BAD_WEBHOOK_BODY" http://127.0.0.1:8020/api/v1/webhooks/payments/TENANT_A/sandbox-amc)"

SETTLEMENT="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"event_type":"settlement","amount_cents":10000,"reference":"SMOKE-SETTLEMENT","idempotency_key":"v09-smoke-settlement","occurred_at":"2026-09-14T10:00:00Z","acknowledged":true}' http://127.0.0.1:8020/api/v1/commissions/events)"
SETTLEMENT_DUPLICATE="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"event_type":"settlement","amount_cents":10000,"reference":"SMOKE-SETTLEMENT","idempotency_key":"v09-smoke-settlement","occurred_at":"2026-09-14T10:00:00Z","acknowledged":true}' http://127.0.0.1:8020/api/v1/commissions/events)"
COLLECTION="$(curl --silent --fail "${HEADERS[@]}" -X POST -d '{"event_type":"collection","amount_cents":6000,"reference":"SMOKE-COLLECTION","idempotency_key":"v09-smoke-collection","occurred_at":"2026-09-14T10:05:00Z","acknowledged":true}' http://127.0.0.1:8020/api/v1/commissions/events)"
OVER_COLLECTION_STATUS="$(curl --silent -o "$RUN_DIR/over-collection.json" -w '%{http_code}' "${HEADERS[@]}" -X POST -d '{"event_type":"collection","amount_cents":5000,"reference":"SMOKE-OVER-COLLECTION","idempotency_key":"v09-smoke-over-collection","occurred_at":"2026-09-14T10:06:00Z","acknowledged":true}' http://127.0.0.1:8020/api/v1/commissions/events)"
FINAL="$(curl --silent --fail "${HEADERS[@]}" http://127.0.0.1:8020/api/v1/payments/overview)"
HEALTH="$(curl --silent --fail http://127.0.0.1:8020/api/v1/health)"

test "$(jq -r '.version' <<<"$HEALTH")" = "0.9.1"
test "$(jq -r '.duplicate' <<<"$FIRST")" = "false"
test "$(jq -r '.duplicate' <<<"$DUPLICATE")" = "true"
test "$(jq -r '.receipt.duplicate_count' <<<"$DUPLICATE")" = "1"
test "$(( $(jq -r '.summary.confirmed_net_recovery_cents' <<<"$BASE") + 101600 ))" = "$(jq -r '.summary.confirmed_net_recovery_cents' <<<"$AFTER_PAYMENT")"
test "$(( $(jq -r '.summary.accrued_commission_cents' <<<"$BASE") + 15240 ))" = "$(jq -r '.summary.accrued_commission_cents' <<<"$AFTER_PAYMENT")"
test "$BAD_WEBHOOK_STATUS" = "401"
test "$(jq -r '.duplicate' <<<"$SETTLEMENT")" = "false"
test "$(jq -r '.duplicate' <<<"$SETTLEMENT_DUPLICATE")" = "true"
test "$(jq -r '.duplicate' <<<"$COLLECTION")" = "false"
test "$OVER_COLLECTION_STATUS" = "409"
test "$(jq -r '.summary.settled_commission_cents' <<<"$FINAL")" = "10000"
test "$(jq -r '.summary.collected_commission_cents' <<<"$FINAL")" = "6000"

jq -n \
  --arg api_version "$(jq -r '.version' <<<"$HEALTH")" \
  --arg receipt_status "$(jq -r '.receipt.status' <<<"$FIRST")" \
  --arg bad_signature_http_status "$BAD_WEBHOOK_STATUS" \
  --arg over_collection_http_status "$OVER_COLLECTION_STATUS" \
  --argjson duplicate_count "$(jq -r '.receipt.duplicate_count' <<<"$DUPLICATE")" \
  --argjson recovery_cents "$(jq -r '.summary.confirmed_net_recovery_cents' <<<"$FINAL")" \
  --argjson accrued_cents "$(jq -r '.summary.accrued_commission_cents' <<<"$FINAL")" \
  --argjson settled_cents "$(jq -r '.summary.settled_commission_cents' <<<"$FINAL")" \
  --argjson collected_cents "$(jq -r '.summary.collected_commission_cents' <<<"$FINAL")" \
  '{api_version:$api_version,receipt_status:$receipt_status,duplicate_count:$duplicate_count,bad_signature_http_status:$bad_signature_http_status,over_collection_http_status:$over_collection_http_status,summary:{recovery_cents:$recovery_cents,accrued_cents:$accrued_cents,settled_cents:$settled_cents,collected_cents:$collected_cents}}'
