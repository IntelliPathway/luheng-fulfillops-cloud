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

export DATABASE_URL="sqlite:///$RUN_DIR/protection-smoke.db"
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
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8022 >"$RUN_DIR/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 40); do
  if curl --silent --fail http://127.0.0.1:8022/api/v1/health >/dev/null; then break; fi
  sleep 0.25
done

ADMIN_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: test-user')
REVIEWER_HEADERS=(-H 'Content-Type: application/json' -H 'X-Tenant-ID: TENANT_A' -H 'X-Actor-ID: Terry')
OPENED="$(curl --silent --fail "${ADMIN_HEADERS[@]}" -X POST -d '{"source_event_id":"V11-SMOKE-001","category":"debt_dispute","reason":"验收发现新的债务异议，必须立即停止主动触达","owner":"ECS 验收人员","sla_hours":4,"acknowledged":true}' http://127.0.0.1:8022/api/v1/cases/C008/protections)"
INCIDENT_ID="$(jq -r '.id' <<<"$OPENED")"
PROPOSED="$(curl --silent --fail "${ADMIN_HEADERS[@]}" -X POST -d '{"resolution_note":"已核对异议材料并完成证据归档，申请重新评估","evidence_refs":["EVIDENCE-V11-SMOKE-001"],"acknowledged":true}' "http://127.0.0.1:8022/api/v1/protections/incidents/$INCIDENT_ID/resolution-proposals")"
VERSION="$(jq -r '.version' <<<"$PROPOSED")"
SELF_REVIEW_STATUS="$(curl --silent -o "$RUN_DIR/self-review.json" -w '%{http_code}' "${ADMIN_HEADERS[@]}" -X POST -d "{\"decision\":\"approve\",\"review_note\":\"独立复核证据一致\",\"expected_version\":$VERSION,\"acknowledged\":true}" "http://127.0.0.1:8022/api/v1/protections/incidents/$INCIDENT_ID/decision")"
STALE_REVIEW_STATUS="$(curl --silent -o "$RUN_DIR/stale-review.json" -w '%{http_code}' "${REVIEWER_HEADERS[@]}" -X POST -d '{"decision":"approve","review_note":"独立复核证据一致","expected_version":1,"acknowledged":true}' "http://127.0.0.1:8022/api/v1/protections/incidents/$INCIDENT_ID/decision")"
APPROVED="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" -X POST -d "{\"decision\":\"approve\",\"review_note\":\"独立复核证据一致，转入重新评估\",\"expected_version\":$VERSION,\"acknowledged\":true}" "http://127.0.0.1:8022/api/v1/protections/incidents/$INCIDENT_ID/decision")"
OVERVIEW="$(curl --silent --fail "${REVIEWER_HEADERS[@]}" http://127.0.0.1:8022/api/v1/protections/overview)"

test "$(jq -r '.status' <<<"$OPENED")" = open
test "$(jq -r '.priority' <<<"$OPENED")" = P0
test "$(jq -r '.status' <<<"$PROPOSED")" = pending_review
test "$SELF_REVIEW_STATUS" = 409
test "$STALE_REVIEW_STATUS" = 409
test "$(jq -r '.status' <<<"$APPROVED")" = resolved
test "$(jq -r '.case_released' <<<"$APPROVED")" = true
test "$(jq -r --arg id "$INCIDENT_ID" '.incidents[] | select(.id==$id) | .reviewed_by' <<<"$OVERVIEW")" = Terry

jq -n \
  --arg incident_id "$INCIDENT_ID" \
  --arg opened_status "$(jq -r '.status' <<<"$OPENED")" \
  --arg proposal_status "$(jq -r '.status' <<<"$PROPOSED")" \
  --arg decision_status "$(jq -r '.status' <<<"$APPROVED")" \
  --arg self_review_http_status "$SELF_REVIEW_STATUS" \
  --arg stale_version_http_status "$STALE_REVIEW_STATUS" \
  '{incident_id:$incident_id,opened_status:$opened_status,proposal_status:$proposal_status,decision_status:$decision_status,self_review_http_status:$self_review_http_status,stale_version_http_status:$stale_version_http_status,activity_auto_resumed:false}'
