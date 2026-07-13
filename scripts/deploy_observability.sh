#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"

NAMESPACE="observability"

if [[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]]; then
  echo \
    "GRAFANA_ADMIN_PASSWORD is not set. " \
    "Source runtime/grafana-admin.env first." \
    >&2

  exit 1
fi

kubectl create namespace "${NAMESPACE}" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl create configmap prometheus-config \
  --namespace "${NAMESPACE}" \
  --from-file=prometheus.yml="${
    PROJECT_ROOT
  }/deploy/observability/prometheus/prometheus.yml" \
  --from-file=compute-optical.rules.yml="${
    PROJECT_ROOT
  }/deploy/observability/prometheus/compute-optical.rules.yml" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl create configmap grafana-provisioning \
  --namespace "${NAMESPACE}" \
  --from-file=datasource.yml="${
    PROJECT_ROOT
  }/deploy/observability/grafana/datasource.yml" \
  --from-file=dashboard-provider.yml="${
    PROJECT_ROOT
  }/deploy/observability/grafana/dashboard-provider.yml" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl create configmap grafana-dashboard \
  --namespace "${NAMESPACE}" \
  --from-file=compute-optical-dashboard.json="${
    PROJECT_ROOT
  }/deploy/observability/grafana/compute-optical-dashboard.json" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl create secret generic grafana-admin \
  --namespace "${NAMESPACE}" \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="${
    GRAFANA_ADMIN_PASSWORD
  }" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

kubectl apply \
  -f "${PROJECT_ROOT}/deploy/observability/stack.yaml"

kubectl rollout restart deployment/prometheus \
  --namespace "${NAMESPACE}"

kubectl rollout restart deployment/grafana \
  --namespace "${NAMESPACE}"

kubectl rollout status deployment/prometheus \
  --namespace "${NAMESPACE}" \
  --timeout=180s

kubectl rollout status deployment/grafana \
  --namespace "${NAMESPACE}" \
  --timeout=180s

echo
echo "Observability stack deployed successfully."
echo
echo "Run:"
echo "  scripts/port_forward_observability.sh"
