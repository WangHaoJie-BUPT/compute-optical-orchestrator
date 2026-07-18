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

  echo "[PASS] Kubernetes ServiceAccount credentials are available."
fi

exec "$@"
