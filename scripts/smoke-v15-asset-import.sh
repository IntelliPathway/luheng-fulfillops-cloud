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

export DATABASE_URL="sqlite:///$RUN_DIR/asset-import-smoke.db"
export AUTH_MODE=development
export ALLOW_DEV_HEADER_AUTH=true
export ALLOW_DEV_TOKEN=true
export PYTHONPATH="$PROJECT_ROOT/backend"

if [[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
else
  PYTHON_BIN=python
fi

cd "$PROJECT_ROOT/backend"
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8024 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8024/api/v1/health >/dev/null; then break; fi
  sleep 0.25
done

MAKER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
REVIEWER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: Terry')
CSV_TEXT=$'package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref,case_status\nPKG_V15,发布验收资产包,C915,2500000,2026-09-01,2027-08-31,COM_V15_V1,1600,CONSENT-C915,待联系'
PREVIEW_BODY="$(jq -n --arg filename 'v15_asset_cases.csv' --arg csv_text "$CSV_TEXT" --arg key 'v15-asset-import-smoke' '{filename:$filename,csv_text:$csv_text,idempotency_key:$key}')"
PREVIEW="$(curl --silent --fail "${MAKER_HEADERS[@]}" -X POST -d "$PREVIEW_BODY" http://127.0.0.1:8024/api/v1/asset-imports/previews)"
BATCH_ID="$(jq -r '.id' <<<"$PREVIEW")"
VERSION="$(jq -r '.version' <<<"$PREVIEW")"
REPLAY="$(curl --silent --fail "${MAKER_HEADERS[@]}" -X POST -d "$PREVIEW_BODY" http://127.0.0.1:8024/api/v1/asset-imports/previews)"
SELF_REVIEW_STATUS="$(curl --silent -o "$RUN_DIR/self-review.json" -w '%{http_code}' "${MAKER_HEADERS[@]}" -X POST -d "{\"expected_version\":$VERSION,\"review_note\":\"本人创建的导入批次不能自行确认\",\"acknowledged\":true}" "http://127.0.0.1:8024/api/v1/asset-imports/$BATCH_ID/commit")"
COMMITTED="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" -X POST -d "{\"expected_version\":$VERSION,\"review_note\":\"已独立复核字段金额与委托期限一致\",\"acknowledged\":true}" "http://127.0.0.1:8024/api/v1/asset-imports/$BATCH_ID/commit")"
RECOMMITTED="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" -X POST -d "{\"expected_version\":$VERSION,\"review_note\":\"已独立复核字段金额与委托期限一致\",\"acknowledged\":true}" "http://127.0.0.1:8024/api/v1/asset-imports/$BATCH_ID/commit")"
LISTED="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" http://127.0.0.1:8024/api/v1/asset-imports)"
HEALTH="$(curl --silent --fail http://127.0.0.1:8024/api/v1/health)"

DB_COUNTS="$(DATABASE_URL="$DATABASE_URL" "$PYTHON_BIN" -c 'from sqlalchemy import create_engine,text; import os,json; e=create_engine(os.environ["DATABASE_URL"]); c=e.connect(); print(json.dumps({"packages":c.scalar(text("select count(*) from asset_packages where package_id=\"PKG_V15\"")),"cases":c.scalar(text("select count(*) from cases where case_id=\"C915\"")),"profiles":c.scalar(text("select count(*) from case_financial_profiles where case_id=\"C915\"")),"rules":c.scalar(text("select count(*) from commission_rules where rule_id=\"COM_V15_V1\""))})); c.close()')"

test "$(jq -r '.version' <<<"$HEALTH")" = "2.0.0"
test "$(jq -r '.status' <<<"$PREVIEW")" = "ready"
test "$(jq -r '.valid_count' <<<"$PREVIEW")" = "1"
test "$(jq -r '.idempotent_replay' <<<"$REPLAY")" = "true"
test "$SELF_REVIEW_STATUS" = "403"
test "$(jq -r '.status' <<<"$COMMITTED")" = "committed"
test "$(jq -r '.version' <<<"$COMMITTED")" = "2"
test "$(jq -r '.idempotent_replay' <<<"$RECOMMITTED")" = "true"
test "$(jq -r '.[0].id' <<<"$LISTED")" = "$BATCH_ID"
test "$(jq -r '.packages' <<<"$DB_COUNTS")" = "1"
test "$(jq -r '.cases' <<<"$DB_COUNTS")" = "1"
test "$(jq -r '.profiles' <<<"$DB_COUNTS")" = "1"
test "$(jq -r '.rules' <<<"$DB_COUNTS")" = "1"

jq -n \
  --arg api_version "$(jq -r '.version' <<<"$HEALTH")" \
  --arg batch_id "$BATCH_ID" \
  --arg status "$(jq -r '.status' <<<"$COMMITTED")" \
  --arg self_review_http_status "$SELF_REVIEW_STATUS" \
  --argjson valid_count "$(jq -r '.valid_count' <<<"$COMMITTED")" \
  --argjson total_claim_balance_cents "$(jq -r '.total_claim_balance_cents' <<<"$COMMITTED")" \
  '{api_version:$api_version,batch_id:$batch_id,status:$status,self_review_http_status:$self_review_http_status,valid_count:$valid_count,total_claim_balance_cents:$total_claim_balance_cents,idempotent_replay:true,atomic_records_verified:true}'
