#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/wanghaojie/projects/compute-optical-orchestrator
NAMESPACE=orchestrator-system
DEPLOYMENT=compute-optical-orchestrator
PVC=compute-optical-orchestrator-data
SEED_POD=runtime-pvc-seeder
SEED_FILE="$PROJECT_ROOT/runtime/runtime-orchestrator.pvc-seed-$(date +%Y%m%d%H%M%S).db"

POD=$(kubectl get pods \
  -n "$NAMESPACE" \
  -l app=compute-optical-orchestrator \
  -o jsonpath='{.items[0].metadata.name}')

echo "Creating a consistent SQLite backup from $POD"
kubectl exec -n "$NAMESPACE" "$POD" -- python -c \
  "import sqlite3; src=sqlite3.connect('/data/runtime-orchestrator.db'); dst=sqlite3.connect('/tmp/runtime-orchestrator.pvc-seed.db'); src.backup(dst); dst.close(); src.close()"
kubectl cp \
  "$NAMESPACE/$POD:/tmp/runtime-orchestrator.pvc-seed.db" \
  "$SEED_FILE"

echo "Applying PVC"
kubectl apply -f "$PROJECT_ROOT/deploy/orchestrator/runtime-pvc.yaml"

kubectl delete pod "$SEED_POD" \
  -n "$NAMESPACE" \
  --ignore-not-found=true \
  --wait=true

echo "Seeding PVC from $SEED_FILE"
kubectl run "$SEED_POD" \
  -n "$NAMESPACE" \
  --image=busybox:1.36 \
  --image-pull-policy=Never \
  --restart=Never \
  --overrides="$(printf '%s' '{
    "spec": {
      "containers": [{
        "name": "runtime-pvc-seeder",
        "image": "busybox:1.36",
        "imagePullPolicy": "Never",
        "command": ["sh", "-c", "sleep 600"],
        "volumeMounts": [{"name": "runtime-data", "mountPath": "/data"}]
      }],
      "volumes": [{
        "name": "runtime-data",
        "persistentVolumeClaim": {"claimName": "compute-optical-orchestrator-data"}
      }]
    }
  }')"
kubectl wait \
  -n "$NAMESPACE" \
  --for=jsonpath='{.status.phase}'=Bound \
  "pvc/$PVC" \
  --timeout=120s
kubectl wait \
  -n "$NAMESPACE" \
  --for=condition=Ready \
  "pod/$SEED_POD" \
  --timeout=120s
kubectl cp \
  "$SEED_FILE" \
  "$NAMESPACE/$SEED_POD:/data/runtime-orchestrator.db"
kubectl exec -n "$NAMESPACE" "$SEED_POD" -- \
  ls -l /data/runtime-orchestrator.db
kubectl delete pod "$SEED_POD" \
  -n "$NAMESPACE" \
  --wait=true

echo "Switching the Deployment to the PVC"
kubectl apply -f "$PROJECT_ROOT/deploy/orchestrator/runtime.yaml"
kubectl rollout status \
  "deployment/$DEPLOYMENT" \
  -n "$NAMESPACE" \
  --timeout=240s

echo "PVC migration completed; seed backup retained at $SEED_FILE"
