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

export DATABASE_URL="sqlite:///$RUN_DIR/repayment-plan-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export SECRET_STORE_BACKEND=local-envelope
export SECRET_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export SECRET_MASTER_KEY_VERSION="smoke-v12"
export PAYMENT_SANDBOX_SECRET="smoke-repayment-secret-never-persist-plaintext"
export ENABLE_PAYMENT_SANDBOX=true
export PAYMENT_WEBHOOK_TOLERANCE_SECONDS=300
export PYTHONPATH="$PROJECT_ROOT/backend"

if [[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
else
  PYTHON_BIN=python
fi

cd "$PROJECT_ROOT/backend"
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8023 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8023/api/v1/health >/dev/null; then break; fi
  sleep 0.25
done

OPERATOR_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
REVIEWER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: Terry')
PLAN_BODY='{"plan_id":"PLAN-V12-SMOKE","total_cents":1200000,"down_payment_cents":240000,"installments":[{"installment_no":1,"due_date":"2026-09-16","due_cents":240000},{"installment_no":2,"due_date":"2026-10-16","due_cents":480000},{"installment_no":3,"due_date":"2026-11-16","due_cents":480000}],"agreement_reference":"AGREEMENT-V12-SMOKE","agreement_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","signed_at":"2026-09-15T00:00:00Z","proposal_reason":"ECS 发布候选验证签约证据、授权策略与分期金额","acknowledged":true}'
PROTECTED_BODY='{"plan_id":"PLAN-V12-PROTECTED","total_cents":1300000,"down_payment_cents":260000,"installments":[{"installment_no":1,"due_date":"2026-09-16","due_cents":260000},{"installment_no":2,"due_date":"2026-10-16","due_cents":520000},{"installment_no":3,"due_date":"2026-11-16","due_cents":520000}],"agreement_reference":"AGREEMENT-V12-PROTECTED","agreement_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","signed_at":"2026-09-15T00:00:00Z","proposal_reason":"验证保护案件无法创建新的履约方案","acknowledged":true}'

PROTECTED_STATUS="$(curl --silent -o "$RUN_DIR/protected.json" -w '%{http_code}' "${OPERATOR_HEADERS[@]}" -X POST -d "$PROTECTED_BODY" http://127.0.0.1:8023/api/v1/cases/C010/repayment-plans)"
PROPOSED="$(curl --silent --fail "${OPERATOR_HEADERS[@]}" -X POST -d "$PLAN_BODY" http://127.0.0.1:8023/api/v1/cases/C008/repayment-plans)"
ROW_ID="$(jq -r '.id' <<<"$PROPOSED")"
VERSION="$(jq -r '.version' <<<"$PROPOSED")"
SELF_REVIEW_STATUS="$(curl --silent -o "$RUN_DIR/self-review.json" -w '%{http_code}' "${OPERATOR_HEADERS[@]}" -X POST -d "{\"decision\":\"approve\",\"review_note\":\"提案人不能审批自己的方案\",\"expected_version\":$VERSION,\"acknowledged\":true}" "http://127.0.0.1:8023/api/v1/repayment-plans/$ROW_ID/decision")"
STALE_REVIEW_STATUS="$(curl --silent -o "$RUN_DIR/stale-review.json" -w '%{http_code}' "${REVIEWER_HEADERS[@]}" -X POST -d '{"decision":"approve","review_note":"验证陈旧方案版本不能获批","expected_version":2,"acknowledged":true}' "http://127.0.0.1:8023/api/v1/repayment-plans/$ROW_ID/decision")"
APPROVED="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" -X POST -d "{\"decision\":\"approve\",\"review_note\":\"独立核验签署摘要、方案金额、期次和资产包授权策略\",\"expected_version\":$VERSION,\"acknowledged\":true}" "http://127.0.0.1:8023/api/v1/repayment-plans/$ROW_ID/decision")"

PAYMENT_BODY='{"event_id":"V12-SMOKE-PAYMENT","event_type":"payment","amount_cents":300000,"currency":"CNY","occurred_at":"2026-09-15T01:00:00Z","case_id":"C008"}'
PAYMENT_TIMESTAMP="$(date +%s)"
PAYMENT_SIGNATURE="$("$PYTHON_BIN" -c 'import sys; from app.financial_ledger import sign_payment_webhook; print(sign_payment_webhook("smoke-repayment-secret-never-persist-plaintext", sys.argv[1], sys.argv[2].encode()))' "$PAYMENT_TIMESTAMP" "$PAYMENT_BODY")"
PAYMENT="$(curl --silent --fail -H 'Content-Type: application/json' -H "X-FulfillOps-Timestamp: $PAYMENT_TIMESTAMP" -H "X-FulfillOps-Signature: $PAYMENT_SIGNATURE" -X POST -d "$PAYMENT_BODY" http://127.0.0.1:8023/api/v1/webhooks/payments/TENANT_A/sandbox-amc)"

REFUND_BODY='{"event_id":"V12-SMOKE-REFUND","event_type":"refund","amount_cents":100000,"currency":"CNY","occurred_at":"2026-09-15T01:05:00Z","case_id":"C008","original_event_id":"V12-SMOKE-PAYMENT"}'
REFUND_TIMESTAMP="$(date +%s)"
REFUND_SIGNATURE="$("$PYTHON_BIN" -c 'import sys; from app.financial_ledger import sign_payment_webhook; print(sign_payment_webhook("smoke-repayment-secret-never-persist-plaintext", sys.argv[1], sys.argv[2].encode()))' "$REFUND_TIMESTAMP" "$REFUND_BODY")"
REFUND="$(curl --silent --fail -H 'Content-Type: application/json' -H "X-FulfillOps-Timestamp: $REFUND_TIMESTAMP" -H "X-FulfillOps-Signature: $REFUND_SIGNATURE" -X POST -d "$REFUND_BODY" http://127.0.0.1:8023/api/v1/webhooks/payments/TENANT_A/sandbox-amc)"

OVERVIEW="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" http://127.0.0.1:8023/api/v1/repayment-plans/overview)"
PLAN="$(jq -c '.plans[] | select(.plan_id == "PLAN-V12-SMOKE")' <<<"$OVERVIEW")"
HEALTH="$(curl --silent --fail http://127.0.0.1:8023/api/v1/health)"

test "$(jq -r '.version' <<<"$HEALTH")" = "1.4.0"
test "$PROTECTED_STATUS" = "409"
test "$(jq -r '.status' <<<"$PROPOSED")" = "pending_review"
test "$SELF_REVIEW_STATUS" = "409"
test "$STALE_REVIEW_STATUS" = "409"
test "$(jq -r '.status' <<<"$APPROVED")" = "active"
test "$(jq -r '.duplicate' <<<"$PAYMENT")" = "false"
test "$(jq -r '.duplicate' <<<"$REFUND")" = "false"
test "$(jq -r '.paid_cents' <<<"$PLAN")" = "200000"
test "$(jq -r '.installments[0].paid_cents' <<<"$PLAN")" = "200000"
test "$(jq -r '.installments[1].paid_cents' <<<"$PLAN")" = "0"
test "$(jq -r '.installments[2].paid_cents' <<<"$PLAN")" = "0"

jq -n \
  --arg api_version "$(jq -r '.version' <<<"$HEALTH")" \
  --arg plan_id "$(jq -r '.plan_id' <<<"$PLAN")" \
  --arg status "$(jq -r '.status' <<<"$PLAN")" \
  --arg protected_http_status "$PROTECTED_STATUS" \
  --arg self_review_http_status "$SELF_REVIEW_STATUS" \
  --arg stale_version_http_status "$STALE_REVIEW_STATUS" \
  --argjson paid_cents "$(jq -r '.paid_cents' <<<"$PLAN")" \
  --argjson remaining_cents "$(jq -r '.remaining_cents' <<<"$PLAN")" \
  '{api_version:$api_version,plan_id:$plan_id,status:$status,protected_http_status:$protected_http_status,self_review_http_status:$self_review_http_status,stale_version_http_status:$stale_version_http_status,paid_cents:$paid_cents,remaining_cents:$remaining_cents,refund_reversed_latest_allocation:true}'
