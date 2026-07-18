#!/usr/bin/env bash
set -euo pipefail

CONTEXT="${KUBERNETES_CONTEXT:-kind-compute-optical}"
NAMESPACE="${ORCHESTRATOR_NAMESPACE:-orchestrator-system}"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"
METRICS_BASE="${METRICS_BASE:-http://127.0.0.1:9100}"
AUTO_PORT_FORWARD="${AUTO_PORT_FORWARD:-1}"
API_KEY="${COMPUTE_OPTICAL_API_KEY:?Set COMPUTE_OPTICAL_API_KEY first}"
RUN_ID="$(date -u +%Y%m%d%H%M%S)"
V2_TASK="demo-v2-${RUN_ID}"
V3_TASK="demo-v3-${RUN_ID}"
PF_API_PID=""
PF_METRICS_PID=""

cleanup() {
  if [[ -n "${PF_API_PID}" ]]; then kill "${PF_API_PID}" 2>/dev/null || true; fi
  if [[ -n "${PF_METRICS_PID}" ]]; then kill "${PF_METRICS_PID}" 2>/dev/null || true; fi
}
trap cleanup EXIT

api() {
  curl --fail --silent --show-error \
    -H "X-API-Key: ${API_KEY}" \
    -H 'Content-Type: application/json' \
    "$@"
}

if [[ "${AUTO_PORT_FORWARD}" == "1" ]]; then
  kubectl --context "${CONTEXT}" -n "${NAMESPACE}" port-forward \
    service/compute-optical-orchestrator-api 8000:8000 >/tmp/compute-optical-api-pf.log 2>&1 &
  PF_API_PID=$!
  kubectl --context "${CONTEXT}" -n "${NAMESPACE}" port-forward \
    service/compute-optical-orchestrator-metrics 9100:9100 >/tmp/compute-optical-metrics-pf.log 2>&1 &
  PF_METRICS_PID=$!
fi

for _ in $(seq 1 30); do
  if curl --fail --silent "${API_BASE}/healthz" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent "${API_BASE}/healthz" >/dev/null

echo '== Profiles =='
api "${API_BASE}/api/v2/task-profiles" | python3 -m json.tool

echo '== v2 synchronous submission =='
api -X POST "${API_BASE}/api/v2/tasks" -d "{
  \"task_id\": \"${V2_TASK}\",
  \"profile_id\": \"busybox-smoke\",
  \"source_access_node\": \"trx Lorient_KMA\",
  \"duration_seconds\": 3
}" | python3 -m json.tool

echo '== v3 asynchronous enqueue =='
api -X POST "${API_BASE}/api/v3/tasks" -d "{
  \"task_id\": \"${V3_TASK}\",
  \"profile_id\": \"busybox-smoke\",
  \"source_access_node\": \"trx Lorient_KMA\",
  \"duration_seconds\": 3,
  \"tenant_id\": \"demo-tenant-b\",
  \"priority\": 60
}" | python3 -m json.tool

echo '== Admission dispatch =='
api -X POST "${API_BASE}/api/v3/queue/dispatch" -d '{}' | python3 -m json.tool

echo '== Queue state =='
api "${API_BASE}/api/v3/queue/${V3_TASK}" | python3 -m json.tool
api "${API_BASE}/api/v3/queue/stats" | python3 -m json.tool

echo '== Kubernetes Jobs =='
kubectl --context "${CONTEXT}" -n compute-optical get jobs,pods -o wide

echo '== Selected metrics =='
curl --fail --silent "${METRICS_BASE}/metrics" \
  | grep -E 'compute_optical_(task_state|admission|spectrum|controller)' \
  | head -n 80 || true

sleep 8

echo '== Runtime tasks and events =='
api "${API_BASE}/api/v1/tasks/${V2_TASK}" | python3 -m json.tool || true
api "${API_BASE}/api/v1/tasks/${V3_TASK}" | python3 -m json.tool || true
api "${API_BASE}/api/v2/tasks/${V2_TASK}/events" | python3 -m json.tool || true
api "${API_BASE}/api/v2/tasks/${V3_TASK}/events" | python3 -m json.tool || true

echo '== Idempotent cleanup requests =='
api -X POST "${API_BASE}/api/v2/tasks/${V2_TASK}/cancel" -d '{}' | python3 -m json.tool || true
api -X POST "${API_BASE}/api/v2/tasks/${V3_TASK}/cancel" -d '{}' | python3 -m json.tool || true

echo "Demo task IDs: ${V2_TASK}, ${V3_TASK}"
