# Runtime performance measurement

Status: measurement harness added and a three-sample smoke baseline recorded.
The sample is too small for a statistically meaningful p95 claim.

## What is already measured

- The v13 acceptance run executed 31 of 31 test scripts successfully in
  307.24 seconds on 2026-07-17.
- The deployed orchestrator Pod was `1/1 Running` with zero restarts after the
  run.
- A two-tenant v3 exercise completed both records through
  `QUEUED -> ADMITTED -> RELEASED`; active queue depth and persistent spectrum
  claims returned to zero.
- The deterministic policy experiment is reported separately in
  `synthetic-policy-baseline.md`. Those acceptance ratios are not live
  Kubernetes or GNPy latency results.

## Live smoke baseline

Measured on 2026-07-18 against the existing single-host three-worker kind
deployment and image `compute-optical-orchestrator:dev-incluster-v13`. Each
profile-controlled workload ran for one second; the client polled every 250 ms.

| Boundary | Samples | Mean | Median | Observed range |
| --- | ---: | ---: | ---: | ---: |
| v2 request to 202 | 3 | 15.328 s | 15.384 s | 14.472-16.129 s |
| v2 202 to RELEASED | 3 | 4.395 s | 4.302 s | 4.279-4.605 s |
| v3 request to durable 202 | 3 | 45.856 ms | 40.440 ms | 23.993-73.136 ms |
| v3 enqueue to ADMITTED | 3 | 16.354 s | 16.312 s | 15.584-17.167 s |
| v3 ADMITTED to RELEASED | 3 | 8.297 s | 6.376 s | 4.017-14.497 s |

All six sampled tasks reached `RELEASED`. The difference between v2 and v3
HTTP response time is expected: v2 performs planning and Job creation before
returning, while v3 only durably enqueues the request. Admission and terminal
wait include the two-second controller interval, scheduling, Job execution,
and port-forward/client overhead. These values are engineering smoke evidence,
not an SLO or production capacity result.

## Live latency harness

Run against the deployed API through a local port-forward:

```bash
export COMPUTE_OPTICAL_API_KEY='replace-with-the-deployed-key'
kubectl --context kind-compute-optical -n orchestrator-system port-forward \
  service/compute-optical-orchestrator-api 8000:8000

python scripts/benchmark_runtime_pipeline.py \
  --mode v2 --samples 20 \
  --output runtime/benchmark-v2.json

python scripts/benchmark_runtime_pipeline.py \
  --mode v3 --samples 20 \
  --output runtime/benchmark-v3.json
```

The harness records client-observed HTTP submission, v3 admission wait, and
runtime terminal wait. Terminal wait includes the configured workload duration
and controller polling interval, so it is not pure scheduler latency.

## Required experiment design

For a publishable or resume-quality latency statement, use at least 30 warm
samples and report p50/p95, mean, standard deviation, hardware, Kubernetes and
GNPy versions, topology size, candidate count, workload profile, controller
interval, and whether caches were warm. Run cold-start measurements separately.

Measure these boundaries independently where possible:

| Boundary | Start | Stop | Main confounders |
| --- | --- | --- | --- |
| GNPy planning | Candidate request begins | Explicit N/M QoT result | Topology size and candidate count |
| v2 submission | HTTP request sent | 202 response received | GNPy, discovery, SQLite, Job API |
| v3 enqueue | HTTP request sent | 202 durable queue response | SQLite contention and auth |
| v3 admission | Queue record created | Record reaches ADMITTED | Fairness, backoff, capacity |
| Kubernetes convergence | Job create acknowledged | Pod terminal condition observed | Image pull and kubelet scheduling |
| Recovery | Controller process becomes ready | Recoverable task converges | Lease timing and controller interval |
| Spectrum conflict | Competing CAS begins | One commit and one rejection | SQLite lock scheduling |

## Interpretation limits

The reference kind cluster is a single-host development environment. Its
latency does not represent a production control plane or physical optical
equipment. Small sample counts are smoke evidence only. Do not combine
synthetic acceptance ratios with live latency into a single performance claim.
