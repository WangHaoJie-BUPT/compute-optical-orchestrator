#!/usr/bin/env bash

set -euo pipefail

cleanup() {
  jobs -pr | xargs -r kill 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Prometheus: http://127.0.0.1:9090"
echo "Grafana:    http://127.0.0.1:3000"
echo
echo "Press Ctrl+C to stop both port forwards."

kubectl port-forward \
  --namespace observability \
  service/prometheus \
  9090:9090 &

kubectl port-forward \
  --namespace observability \
  service/grafana \
  3000:3000 &

wait
