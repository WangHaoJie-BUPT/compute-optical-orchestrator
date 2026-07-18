# Demo runbook

The demo proves a complete control-plane path: authenticated submission,
compute-optical planning, durable state, Kubernetes execution, admission,
metrics, and cleanup. It does not claim physical optical provisioning.

## Prerequisites

- The reference kind cluster and v13 image are deployed as described in the
  root README.
- `kubectl`, `curl`, and `python3` are available.
- `COMPUTE_OPTICAL_API_KEY` contains the same value as the Kubernetes Secret.
- The `busybox:1.36` image is already loaded into the kind cluster.

## Run

From the repository root:

```bash
export COMPUTE_OPTICAL_API_KEY='replace-with-the-deployed-key'
scripts/demo.sh
```

The script starts temporary API and metrics port-forwards unless
`AUTO_PORT_FORWARD=0` is set. It then:

1. checks health and reads the profile allowlist;
2. submits one synchronous v2 task;
3. submits one asynchronous v3 task for a second tenant;
4. triggers a dispatch cycle and prints queue/runtime evidence;
5. prints selected Prometheus metrics and Kubernetes Jobs;
6. requests cleanup for non-terminal demo tasks.

Override endpoints when port-forwarding is managed separately:

```bash
AUTO_PORT_FORWARD=0 \
API_BASE=http://127.0.0.1:8000 \
METRICS_BASE=http://127.0.0.1:9100 \
scripts/demo.sh
```

## Evidence to capture

For an interview recording or release note, retain:

- v2 response: `task_id`, `state`, `selected_site_id`, `selected_node_name`;
- v3 sequence: `QUEUED` to `DISPATCHING` to `ADMITTED`;
- Kubernetes Job, Pod, Node and `compute-site` label;
- ordered task events and final runtime state;
- queue active count and spectrum claim count returning to zero;
- controller and queue metrics with no error increase.

Do not expose the API key, kubeconfig, SQLite database, private registry
credentials, cluster addresses, or raw logs containing internal identifiers.

## Expected cleanup

After both workloads have finished and reconciliation has run, the runtime
tasks reach `RELEASED`, the v3 active queue count is zero, and no active
spectrum claim remains. A `RELEASE_FAILED` state is recoverable and should be
treated as a failed demo until a later controller pass reaches a terminal
state.
