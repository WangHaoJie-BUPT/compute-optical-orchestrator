# Durable admission queue

The v3 API separates request acceptance from expensive compute-optical
planning. The existing v2 synchronous endpoint remains available for backward
compatibility.

## Why admission is separate

A temporary lack of accelerator or spectrum capacity should not force a user
to resubmit a request or create a terminal `BLOCKED` runtime task. Admission
records are persisted before planning, can survive process restarts, and apply
backpressure before Kubernetes or GNPy work begins.

## State model

```text
QUEUED -> DISPATCHING -> ADMITTED
   ^           |
   |           +------> REJECTED
   +--- retry -+

QUEUED -> CANCELLED
```

`DISPATCHING` claims contain an expiry. A controller only recovers expired
claims, so a second process cannot immediately steal work from a live worker.
The admission controller has a separate SQLite lease from runtime
reconciliation.

## Selection policy

Selection is intentionally deterministic:

1. Consider `QUEUED` records whose retry deadline has passed.
2. Select the tenant least recently chosen by the controller.
3. Inside that tenant, select the largest effective priority.
4. Effective priority is the submitted priority plus one point for every
   configured aging interval spent waiting.
5. Break remaining ties by enqueue time and task ID.

This prevents a busy tenant from monopolizing admission while ensuring that
old low-priority work eventually outranks newer work from the same tenant. It
does not claim to implement Dominant Resource Fairness.

## Backpressure and retry

The server enforces a global active-queue limit and a per-tenant limit inside
the same SQLite transaction as enqueue. Limit violations return HTTP 429.
Capacity misses are requeued with capped exponential backoff without creating
a terminal runtime task. Non-capacity failures are retried a bounded number of
times, then rejected.

Deployment flags:

```text
--admission-aging-seconds
--admission-dispatch-timeout-seconds
--admission-lease-seconds
--admission-max-active-total
--admission-max-active-per-tenant
--admission-max-dispatch-per-cycle
```

## API

All endpoints require `X-API-Key`:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v3/tasks` | Enqueue a profile-controlled task |
| `GET` | `/api/v3/queue` | List or filter queue records |
| `GET` | `/api/v3/queue/stats` | Read aggregate state counts |
| `GET` | `/api/v3/queue/{task_id}` | Read one queue record |
| `POST` | `/api/v3/queue/{task_id}/cancel` | Cancel queued work |
| `POST` | `/api/v3/queue/dispatch` | Trigger one leased dispatch cycle |

Prometheus exports queue state counts, active tenant count, and the oldest
active wait time. Alerts cover sustained near-capacity depth and long waits.

## Scope

This release does not implement preemption, gang admission, hierarchical
projects, per-tenant resource budgets, or multi-resource DRF. Those require a
resource-accounting contract and workload-level scheduling semantics beyond a
single-task FIFO replacement.
