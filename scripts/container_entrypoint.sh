#!/bin/sh

set -eu

MODE="$(
  printf '%s' "${KUBERNETES_CLIENT_MODE:-auto}" \
  | tr '[:upper:]' '[:lower:]'
)"

if [ "${MODE}" = "incluster" ] \
  || [ "${MODE}" = "in-cluster" ]; then

  SERVICE_ACCOUNT_DIR="/var/run/secrets/kubernetes.io/serviceaccount"
  TOKEN_FILE="${SERVICE_ACCOUNT_DIR}/token"
  CA_FILE="${SERVICE_ACCOUNT_DIR}/ca.crt"

  if [ ! -r "${TOKEN_FILE}" ]; then
    echo "ServiceAccount token unavailable: ${TOKEN_FILE}" >&2
    exit 1
  fi

  if [ ! -r "${CA_FILE}" ]; then
    echo "ServiceAccount CA unavailable: ${CA_FILE}" >&2
    exit 1
  fi

  if [ -z "${KUBERNETES_SERVICE_HOST:-}" ]; then
    echo "KUBERNETES_SERVICE_HOST is not set." >&2
    exit 1
  fi

  API_PORT="${KUBERNETES_SERVICE_PORT_HTTPS:-${KUBERNETES_SERVICE_PORT:-443}}"
  CONTEXT_NAME="${KUBERNETES_CONTEXT_NAME:-kind-compute-optical}"
  RUNTIME_NAMESPACE="${KUBERNETES_RUNTIME_NAMESPACE:-compute-optical}"
  KUBECONFIG_FILE="/tmp/incluster-kubeconfig"

  rm -f "${KUBECONFIG_FILE}"

  kubectl config \
    --kubeconfig="${KUBECONFIG_FILE}" \
    set-cluster "${CONTEXT_NAME}" \
    --server="https://${KUBERNETES_SERVICE_HOST}:${API_PORT}" \
    --certificate-authority="${CA_FILE}" \
    --embed-certs=true \
    >/dev/null

  kubectl config \
    --kubeconfig="${KUBECONFIG_FILE}" \
    set-credentials compute-optical-orchestrator \
    --token="$(cat "${TOKEN_FILE}")" \
    >/dev/null

  kubectl config \
    --kubeconfig="${KUBECONFIG_FILE}" \
    set-context "${CONTEXT_NAME}" \
    --cluster="${CONTEXT_NAME}" \
    --user=compute-optical-orchestrator \
    --namespace="${RUNTIME_NAMESPACE}" \
    >/dev/null

  kubectl config \
    --kubeconfig="${KUBECONFIG_FILE}" \
    use-context "${CONTEXT_NAME}" \
    >/dev/null

  chmod 600 "${KUBECONFIG_FILE}"
  export KUBECONFIG="${KUBECONFIG_FILE}"

  echo "Generated in-cluster kubeconfig:"
  echo "  context=${CONTEXT_NAME}"
  echo "  namespace=${RUNTIME_NAMESPACE}"
  echo "  api-server=https://${KUBERNETES_SERVICE_HOST}:${API_PORT}"

  kubectl \
    --context "${CONTEXT_NAME}" \
    get nodes -o name \
    >/dev/null

  echo "[PASS] Kubernetes API connection verified."
fi

exec "$@"
