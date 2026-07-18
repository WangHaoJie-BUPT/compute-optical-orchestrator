# Architecture and lifecycle contracts

This document describes the v13 working-tree architecture. The public release
version should be `v0.1.0`; `v13` is an internal engineering milestone, not a
semantic version.

## Logical architecture

```mermaid
flowchart LR
    CLIENT[API client] --> AUTH[API-key middleware]
    AUTH --> V2[v2 synchronous API]
    AUTH --> V3[v3 admission API]
    V3 --> AQ[(Admission queue)]
    AQ --> AC[Admission controller]
    AC --> PLAN[Joint planner]
    V2 --> PLAN
    PLAN --> KD[Kubernetes discovery]
    PLAN --> GNPY[GNPy path and QoT]
    PLAN --> ORM[Optical resource manager]
    ORM --> DB[(SQLite WAL and spectrum claims)]
    PLAN --> JOB[Kubernetes Job API]
    JOB --> K8S[Kubernetes cluster]
    RC[Runtime reconciliation controller] --> K8S
    RC --> DB
    DB --> METRICS[Prometheus collector]
    KD --> METRICS
    METRICS --> PROM[Prometheus]
    PROM --> GRAFANA[Grafana and alerts]
```

The deployment is intentionally single-replica. SQLite, a RWO PVC, the
in-memory optical occupancy model, and process-local operation locking are a
coherent single-controller design, not a multi-replica control plane.

## v2 synchronous submission

```mermaid
sequenceDiagram
    participant C as Client
    participant A as v2 API
    participant D as SQLite
    participant P as Joint planner
    participant K as Kubernetes API
    participant G as GNPy
    participant R as Runtime controller

    C->>A: POST /api/v2/tasks
    A->>A: Authenticate and expand safe profile
    A->>D: Create PENDING task
    A->>D: CAS PENDING to PLANNING
    A->>K: Read Nodes and active Pod requests
    loop Each compute candidate
        A->>G: Discover path and slot width
        A->>G: Verify explicit N/M and QoT
    end
    A->>D: Atomically persist plan and slot claims
    D-->>A: RESERVED
    A->>D: CAS RESERVED to SUBMITTING
    A->>K: Create deterministic Job
    A->>D: Persist execution and RUNNING
    A-->>C: 202 with selected site and node
    R->>K: Reconcile Job and Pod state
    R->>D: COMPLETED then RELEASING
    R->>D: Remove claims and persist RELEASED
```

If Job creation succeeds but execution persistence fails, the service deletes
the deterministic Job and releases the optical reservation. A later leased
reconciliation pass handles process interruption in `RESERVED`, `SUBMITTING`,
`RUNNING`, `COMPLETED`, `RELEASING`, or `RELEASE_FAILED`.

## v3 asynchronous admission

```mermaid
sequenceDiagram
    participant C as Client
    participant A as v3 API
    participant Q as SQLite queue
    participant AC as Admission controller
    participant S as Runtime service

    C->>A: POST /api/v3/tasks
    A->>A: Authenticate and expand safe profile
    A->>Q: Enqueue with tenant and priority
    Q-->>A: Durable QUEUED record
    A-->>C: 202 immediately
    AC->>Q: Acquire controller lease
    AC->>Q: Claim fair and eligible task
    Q-->>AC: DISPATCHING with expiry
    AC->>S: Submit through v2 runtime path
    alt Capacity unavailable
        AC->>Q: Requeue with capped backoff
    else Accepted by runtime
        AC->>Q: Mark ADMITTED and runtime state
    else Permanent or exhausted failure
        AC->>Q: Mark REJECTED
    end
```

Tenant rotation is fair across active tenants. Priority aging applies inside
one tenant. This is not Dominant Resource Fairness and does not implement
preemption, gang admission, or hierarchical quotas.

## Runtime state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> PLANNING
    PLANNING --> RESERVED
    RESERVED --> SUBMITTING
    SUBMITTING --> RUNNING
    RUNNING --> COMPLETED
    COMPLETED --> RELEASING
    RELEASING --> RELEASED
    RELEASING --> RELEASE_FAILED
    RELEASE_FAILED --> RELEASING
    PENDING --> FAILED
    PLANNING --> BLOCKED
    PLANNING --> FAILED
    RESERVED --> FAILED
    SUBMITTING --> FAILED
    RUNNING --> FAILED
    PENDING --> RELEASING
    PLANNING --> RELEASING
    RESERVED --> RELEASING
    SUBMITTING --> RELEASING
    RUNNING --> RELEASING
    BLOCKED --> RELEASING
    RELEASING --> CANCELLED
```

All repository updates validate the transition graph. Mutable runtime rows
carry an incrementing version and use compare-and-swap updates so stale
controllers cannot silently overwrite newer state.

## Admission state machine

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> DISPATCHING: leased claim
    DISPATCHING --> ADMITTED: runtime accepted
    DISPATCHING --> QUEUED: retry and backoff
    DISPATCHING --> REJECTED: permanent failure
    QUEUED --> CANCELLED: user cancellation
```

`DISPATCHING` records contain a claim expiry. Recovery only reclaims an
expired record, preventing an active worker from being immediately preempted.

## Persistence and ownership

| Resource | Source of truth | Protection |
| --- | --- | --- |
| Compute availability | Kubernetes Nodes minus active Pod requests | Live discovery before every plan |
| Task lifecycle | SQLite runtime task row | Transition validation and CAS version |
| Optical allocation | SQLite plan plus in-memory occupancy | Unique link/slot claim rows |
| Kubernetes execution | Kubernetes Job and Pod | Deterministic Job name and reconciliation |
| Admission | SQLite queue row | Transactional limits, lease and claim expiry |
| Controller leadership | SQLite lease row | Named renewable lease |

## Failure boundary

The prototype configures no physical optical device. GNPy validates a modeled
path and QoT; the selected spectrum is a control-plane claim. Production use
would require a southbound optical controller, distributed persistence,
Kubernetes-native leader election, admission policy, secret rotation, and
operational SLOs.
