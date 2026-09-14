#!/usr/bin/env bash
set -euo pipefail

smoke_tmp="$(mktemp -d)"
api_pid=""
web_pid=""
task_python="${PYTHON_BIN:-python3}"

cleanup() {
  [[ -n "$web_pid" ]] && kill "$web_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
  rm -rf "$smoke_tmp"
}
trap cleanup EXIT

(
  cd backend
  DATABASE_URL="sqlite:///$smoke_tmp/luheng-smoke.db" CORS_ORIGINS=http://127.0.0.1:4187 "$task_python" -m uvicorn app.main:app --host 127.0.0.1 --port 8010
) >"$smoke_tmp/api.log" 2>&1 &
api_pid=$!

VITE_API_BASE_URL=http://127.0.0.1:8010/api/v1 npm run dev -- --host 127.0.0.1 --port 4187 >"$smoke_tmp/web.log" 2>&1 &
web_pid=$!

for _ in {1..40}; do
  if curl -fsS http://127.0.0.1:8010/api/v1/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:4187/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

base_url="http://127.0.0.1:4187"
actor_headers=(-H "X-Tenant-ID: TENANT_A" -H "X-Actor-ID: Terry" -H "Content-Type: application/json")

page_title="$(curl -fsS "$base_url/" | rg -o '<title>[^<]+' | head -1 | sed 's/<title>//')"
session_json="$(curl -fsS "${actor_headers[@]}" http://127.0.0.1:8010/api/v1/auth/session)"
gateway_json="$(curl -fsS "${actor_headers[@]}" http://127.0.0.1:8010/api/v1/agents/gateway)"
agent_session_id="$(curl -fsS "${actor_headers[@]}" -X POST http://127.0.0.1:8010/api/v1/agents/sessions -d '{"scope_type":"global","title":"HTTP 冒烟"}' | jq -r .id)"
job_id="$(curl -fsS "${actor_headers[@]}" -X POST "http://127.0.0.1:8010/api/v1/agents/sessions/$agent_session_id/messages" -d '{"content":"本月回款和佣金是多少？","idempotency_key":"http-smoke-chatbi-v040"}' | jq -r .id)"

job_json=""
for _ in {1..20}; do
  job_json="$(curl -fsS "${actor_headers[@]}" "http://127.0.0.1:8010/api/v1/jobs/$job_id")"
  job_status="$(jq -r .status <<<"$job_json")"
  [[ "$job_status" == "succeeded" || "$job_status" == "failed" ]] && break
  sleep 0.1
done

token="$(curl -fsS -H 'Content-Type: application/json' -X POST http://127.0.0.1:8010/api/v1/auth/dev-token -d '{"actor_id":"test-operator"}' | jq -r .access_token)"
bearer_role="$(curl -fsS -H "Authorization: Bearer $token" -H 'X-Tenant-ID: TENANT_A' http://127.0.0.1:8010/api/v1/auth/session | jq -r .role)"
runtime_json="$(curl -fsS "${actor_headers[@]}" "http://127.0.0.1:8010/api/v1/agents/sessions/$agent_session_id/runtime")"

jq -n \
  --arg page_title "$page_title" \
  --arg auth_role "$(jq -r .role <<<"$session_json")" \
  --arg auth_mode "$(jq -r .auth_mode <<<"$session_json")" \
  --arg bearer_role "$bearer_role" \
  --arg gateway "$(jq -r '.provider + " / " + .mode' <<<"$gateway_json")" \
  --arg job_id "$job_id" \
  --arg job_status "$(jq -r .status <<<"$job_json")" \
  --arg answer "$(jq -r .result.answer.title <<<"$job_json")" \
  --arg runtime "$(jq -r '.provider_session_id + " / turn " + (.turn_count|tostring) + " / cursor " + (.event_cursor|tostring)' <<<"$runtime_json")" \
  '{page_title:$page_title,auth_role:$auth_role,auth_mode:$auth_mode,bearer_role:$bearer_role,gateway:$gateway,job_id:$job_id,job_status:$job_status,answer:$answer,runtime:$runtime}'
