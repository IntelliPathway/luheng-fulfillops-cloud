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

export DATABASE_URL="sqlite:///$RUN_DIR/reconciliation-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export SECRET_STORE_BACKEND=local-envelope
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v10"
export PAYMENT_SANDBOX_SECRET="smoke-payment-secret-never-persist-plaintext"
export ENABLE_PAYMENT_SANDBOX=true
export PYTHONPATH="$PROJECT_ROOT/backend"

if [[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
else
  PYTHON_BIN="python"
fi

cd "$PROJECT_ROOT/backend"
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8021 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8021/api/v1/health >/dev/null; then
    break
  fi
  sleep 0.25
done

ADMIN_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
OPERATOR_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-operator')
BASE="$(curl --silent --fail "${ADMIN_HEADERS[@]}" http://127.0.0.1:8021/api/v1/payments/overview)"
RECEIPT_RESPONSE="$(curl --silent --fail "${OPERATOR_HEADERS[@]}" -X POST -d '{"case_id":"C999","amount_cents":68800,"idempotency_key":"v10-smoke-unmatched"}' http://127.0.0.1:8021/api/v1/payments/sandbox-receipts)"
RECEIPT_ID="$(jq -r '.receipt.id' <<<"$RECEIPT_RESPONSE")"
CANDIDATES="$(curl --silent --fail "${OPERATOR_HEADERS[@]}" "http://127.0.0.1:8021/api/v1/payments/receipts/$RECEIPT_ID/candidates")"
LEGACY_STATUS="$(curl --silent -o "$RUN_DIR/legacy.json" -w '%{http_code}' "${ADMIN_HEADERS[@]}" -X POST -d '{"case_id":"C002","acknowledged":true}' "http://127.0.0.1:8021/api/v1/payments/receipts/$RECEIPT_ID/match")"
PROPOSAL="$(curl --silent --fail "${OPERATOR_HEADERS[@]}" -X POST -d '{"case_id":"C002","reason":"已核对付款附言与签署方案编号一致","acknowledged":true}' "http://127.0.0.1:8021/api/v1/payments/receipts/$RECEIPT_ID/reconciliations")"
REVIEW_ID="$(jq -r '.id' <<<"$PROPOSAL")"
VERSION_CONFLICT_STATUS="$(curl --silent -o "$RUN_DIR/version.json" -w '%{http_code}' "${ADMIN_HEADERS[@]}" -X POST -d '{"decision":"approve","review_note":"独立复核证据一致","expected_version":2,"acknowledged":true}' "http://127.0.0.1:8021/api/v1/payments/reconciliations/$REVIEW_ID/decision")"
APPROVED="$(curl --silent --fail "${ADMIN_HEADERS[@]}" -X POST -d '{"decision":"approve","review_note":"独立复核证据一致","expected_version":1,"acknowledged":true}' "http://127.0.0.1:8021/api/v1/payments/reconciliations/$REVIEW_ID/decision")"
FINAL="$(curl --silent --fail "${ADMIN_HEADERS[@]}" http://127.0.0.1:8021/api/v1/payments/overview)"

test "$(jq -r '.receipt.status' <<<"$RECEIPT_RESPONSE")" = "unmatched"
test "$(jq -r 'length > 0' <<<"$CANDIDATES")" = "true"
test "$LEGACY_STATUS" = "409"
test "$(jq -r '.status' <<<"$PROPOSAL")" = "pending_review"
test "$VERSION_CONFLICT_STATUS" = "409"
test "$(jq -r '.status' <<<"$APPROVED")" = "approved"
test "$(jq -r '.reviewed_by' <<<"$APPROVED")" = "test-user"
test "$(( $(jq -r '.summary.confirmed_net_recovery_cents' <<<"$BASE") + 68800 ))" = "$(jq -r '.summary.confirmed_net_recovery_cents' <<<"$FINAL")"
test "$(( $(jq -r '.summary.accrued_commission_cents' <<<"$BASE") + 10320 ))" = "$(jq -r '.summary.accrued_commission_cents' <<<"$FINAL")"
test "$(jq -r '.summary.pending_receipt_count' <<<"$FINAL")" = "0"

jq -n \
  --arg receipt_status "$(jq -r '.receipt.status' <<<"$RECEIPT_RESPONSE")" \
  --arg proposal_status "$(jq -r '.status' <<<"$PROPOSAL")" \
  --arg decision_status "$(jq -r '.status' <<<"$APPROVED")" \
  --arg legacy_http_status "$LEGACY_STATUS" \
  --arg stale_version_http_status "$VERSION_CONFLICT_STATUS" \
  --argjson candidate_count "$(jq -r 'length' <<<"$CANDIDATES")" \
  --argjson recovery_cents "$(jq -r '.summary.confirmed_net_recovery_cents' <<<"$FINAL")" \
  --argjson accrued_commission_cents "$(jq -r '.summary.accrued_commission_cents' <<<"$FINAL")" \
  '{receipt_status:$receipt_status,proposal_status:$proposal_status,decision_status:$decision_status,candidate_count:$candidate_count,legacy_http_status:$legacy_http_status,stale_version_http_status:$stale_version_http_status,recovery_cents:$recovery_cents,accrued_commission_cents:$accrued_commission_cents}'
