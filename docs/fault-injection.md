# Fault-injection evidence

The fault suite converts existing recovery and concurrency tests into one
auditable run. It distinguishes automated evidence from scenarios that still
need a controlled live-cluster experiment.

## Verified baseline

Measured on 2026-07-18 with image
`compute-optical-orchestrator:dev-incluster-v13`:

- 6/6 deterministic local scenarios passed in the consolidated runner;
- runtime restart restored one optical allocation, blocked a duplicate
  controller lease, and converged the Job from `RUNNING` to `RELEASED`;
- the official Kubernetes Python Client integration created a Job, observed
  its Pod and Node, and completed successfully;
- the orchestrator Pod remained `1/1 Running` with zero restarts.

This is 8/8 automated scenarios. The two cluster scenarios were also executed
directly so their full console evidence, not only an aggregate return code,
was inspected.

## Run

Local deterministic scenarios:

```bash
python scripts/run_fault_injection_suite.py \
  --output runtime/fault-injection-local.json
```

Add kind-dependent scenarios:

```bash
python scripts/run_fault_injection_suite.py \
  --include-cluster \
  --output runtime/fault-injection-cluster.json
```

## Automated coverage

| Injected condition | Expected invariant | Evidence |
| --- | --- | --- |
| Job created, database save fails | Job deleted and optical allocation released | `test_submission_failure_compensation.py` |
| Release finalization fails once | Task remains recoverable and retry completes | `test_retryable_finalize_failure.py` |
| Two plans claim the same link/slots | Exactly one atomic commit succeeds | `test_persistent_spectrum_claims.py` |
| Two writers update one task version | Stale compare-and-swap update is rejected | `test_runtime_state_versioning.py` |
| Admission claim/retry contention | Limits, fairness, expiry and backoff remain durable | `test_admission_queue.py` |
| Kubernetes client returns errors | Adapter translates boundary failures consistently | `test_kubernetes_api_adapter_unit.py` |
| Runtime process restarts | Allocation is restored and task converges | `test_runtime_reconciliation_controller.py` |
| Duplicate runtime controller | Foreign active lease prevents a second controller | `test_runtime_reconciliation_controller.py` |

## Live experiments still required

These are not represented as completed evidence until their commands and
results are captured from a dedicated cluster:

| Scenario | Injection | Pass condition |
| --- | --- | --- |
| Kubernetes API outage | Temporarily block API connectivity during discovery and Job operations | No duplicate Job; task remains recoverable; error metric rises |
| GNPy timeout | Wrap or terminate the planning subprocess at a fixed timeout | No slot claim or Job leak; v3 retries or rejects by policy |
| Crash in DISPATCHING | Kill the Pod after queue claim, before runtime submit | Claim is recovered only after expiry; one runtime task exists |
| Crash in SUBMITTING | Kill the Pod after deterministic Job create | Reconciliation rediscovers Job and persists one execution |
| Crash in RELEASING | Kill the Pod after state transition | Retry reaches terminal state and claims return to zero |
| PVC migration rollback | Snapshot PVC, deploy migration, then restore snapshot | Schema and active allocations recover without silent loss |

## Result recording contract

Every live injection report should include image digest, Git commit, cluster
versions, exact injection time, pre/post task rows, Job UID, controller logs,
relevant Prometheus samples, spectrum-claim count, recovery duration, and the
cleanup result. A screenshot alone is not sufficient evidence.
