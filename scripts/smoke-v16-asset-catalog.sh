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

export DATABASE_URL="sqlite:///$RUN_DIR/asset-catalog-smoke.db"
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
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8025 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8025/api/v1/health >/dev/null; then break; fi
  sleep 0.25
done

VIEWER_HEADERS=(-H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-viewer')
MAKER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
REVIEWER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: Terry')

HEALTH="$(curl --silent --fail http://127.0.0.1:8025/api/v1/health)"
PACKAGES="$(curl --silent --fail "${VIEWER_HEADERS[@]}" 'http://127.0.0.1:8025/api/v1/asset-packages?page=1&page_size=1')"
CASES="$(curl --silent --fail "${VIEWER_HEADERS[@]}" 'http://127.0.0.1:8025/api/v1/cases?package_id=PKG_B&view=blocked&sort=balance_desc&page=1&page_size=8')"
DETAIL="$(curl --silent --fail "${VIEWER_HEADERS[@]}" http://127.0.0.1:8025/api/v1/cases/C001)"
CROSS_TENANT_STATUS="$(curl --silent -o "$RUN_DIR/cross-tenant.json" -w '%{http_code}' -H 'X-Tenant-ID: TENANT_B' -H 'X-Actor-ID: test-viewer' http://127.0.0.1:8025/api/v1/cases/C001)"

CSV_TEXT=$'package_id,package_title,case_id,claim_balance_cents,mandate_start,mandate_end,commission_rule_id,commission_rate_bps,contact_basis_ref\nPKG_V16,目录冒烟资产包,C916,3600000,2026-09-01,2027-08-31,COM_V16_V1,1750,CONSENT-C916'
PREVIEW_BODY="$(jq -n --arg filename 'v16_catalog.csv' --arg csv_text "$CSV_TEXT" --arg key 'v16-catalog-import-smoke' '{filename:$filename,csv_text:$csv_text,idempotency_key:$key}')"
PREVIEW="$(curl --silent --fail "${MAKER_HEADERS[@]}" -X POST -d "$PREVIEW_BODY" http://127.0.0.1:8025/api/v1/asset-imports/previews)"
BATCH_ID="$(jq -r '.id' <<<"$PREVIEW")"
VERSION="$(jq -r '.version' <<<"$PREVIEW")"
curl --silent --fail "${REVIEWER_HEADERS[@]}" -X POST -d "{\"expected_version\":$VERSION,\"review_note\":\"已独立复核目录冒烟导入数据\",\"acknowledged\":true}" "http://127.0.0.1:8025/api/v1/asset-imports/$BATCH_ID/commit" >/dev/null
IMPORTED_PACKAGE="$(curl --silent --fail "${VIEWER_HEADERS[@]}" 'http://127.0.0.1:8025/api/v1/asset-packages?query=PKG_V16')"
IMPORTED_CASE="$(curl --silent --fail "${VIEWER_HEADERS[@]}" http://127.0.0.1:8025/api/v1/cases/C916)"

test "$(jq -r '.version' <<<"$HEALTH")" = "1.6.0"
test "$(jq -r '.total' <<<"$PACKAGES")" = "2"
test "$(jq -r '.page_size' <<<"$PACKAGES")" = "1"
test "$(jq -r '.total' <<<"$CASES")" = "2"
test "$(jq -r '.facets.blocked' <<<"$CASES")" = "2"
test "$(jq -r '.data_source' <<<"$DETAIL")" = "server-authoritative"
test "$(jq -r '.confirmed_net_recovery_cents' <<<"$DETAIL")" = "1000000"
test "$CROSS_TENANT_STATUS" = "404"
test "$(jq -r '.items[0].policy_status' <<<"$IMPORTED_PACKAGE")" = "draft"
test "$(jq -r '.data_completeness_score' <<<"$IMPORTED_CASE")" = "100"
test "$(jq -r '.source_import_batch_id' <<<"$IMPORTED_CASE")" = "$BATCH_ID"

jq -n \
  --arg api_version "$(jq -r '.version' <<<"$HEALTH")" \
  --arg imported_case "$(jq -r '.case_id' <<<"$IMPORTED_CASE")" \
  --arg data_source "$(jq -r '.data_source' <<<"$IMPORTED_CASE")" \
  --arg cross_tenant_http_status "$CROSS_TENANT_STATUS" \
  --argjson packages_total "$(jq -r '.total' <<<"$PACKAGES")" \
  --argjson blocked_cases "$(jq -r '.total' <<<"$CASES")" \
  '{api_version:$api_version,packages_total:$packages_total,blocked_cases:$blocked_cases,imported_case:$imported_case,data_source:$data_source,cross_tenant_http_status:$cross_tenant_http_status,server_pagination_verified:true}'
