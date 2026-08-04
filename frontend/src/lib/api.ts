import type {
  QueueTask,
  Site,
  Snapshot,
  SpectrumBand,
  Task,
  TaskEvent,
  TaskProfile,
  TaskSubmission,
} from "../types";

interface ApiOptions {
  apiKey: string;
  signal?: AbortSignal;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options: ApiOptions, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (options.apiKey) headers.set("X-API-Key", options.apiKey);
  if (init?.body) headers.set("Content-Type", "application/json");

  const response = await fetch(path, {
    ...init,
    headers,
    signal: options.signal,
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail) message = JSON.stringify(body.detail);
    } catch {
      // Preserve the HTTP status when an endpoint returns plain text.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

type RawTask = {
  task_id: string;
  state: string;
  version: number;
  source_access_node: string;
  cpu_request_m: number;
  memory_request_mib: number;
  gpu_request: number;
  accelerator_resource_name: string;
  accelerator_product: string | null;
  bandwidth_gbps: number;
  selected_site_id: string | null;
  selected_node_name: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
  optical: null | {
    site_id: string;
    network_access_node: string;
    n: number;
    m: number;
    fiber_links: string[];
    route: string[];
    gsnr_db: number;
    total_score: number;
  };
  execution: null | {
    job_name: string;
    namespace: string;
    pod_name: string | null;
    node_name: string | null;
    phase: string;
    succeeded: boolean | null;
  };
};

function mapEvent(raw: Record<string, unknown>): TaskEvent {
  return {
    id: Number(raw.event_id),
    type: String(raw.event_type),
    previousState: raw.previous_state ? String(raw.previous_state) : null,
    newState: raw.new_state ? String(raw.new_state) : null,
    source: String(raw.source),
    message: raw.message ? String(raw.message) : "State updated.",
    createdAt: String(raw.created_at),
  };
}

function mapTask(raw: RawTask, events: TaskEvent[], queue?: QueueTask): Task {
  return {
    id: raw.task_id,
    tenantId: queue?.tenantId ?? "runtime",
    priority: queue?.priority ?? 50,
    profileId: "managed-profile",
    state: raw.state,
    version: raw.version,
    sourceAccessNode: raw.source_access_node,
    selectedSiteId: raw.selected_site_id,
    selectedNodeName: raw.selected_node_name,
    cpuRequestM: raw.cpu_request_m,
    memoryRequestMib: raw.memory_request_mib,
    gpuRequest: raw.gpu_request,
    acceleratorResourceName: raw.accelerator_resource_name,
    acceleratorProduct: raw.accelerator_product,
    bandwidthGbps: raw.bandwidth_gbps,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    failureReason: raw.failure_reason,
    optical: raw.optical
      ? {
          siteId: raw.optical.site_id,
          accessNode: raw.optical.network_access_node,
          n: raw.optical.n,
          m: raw.optical.m,
          route: raw.optical.route,
          fiberLinks: raw.optical.fiber_links,
          gsnrDb: raw.optical.gsnr_db,
          totalScore: raw.optical.total_score,
        }
      : null,
    execution: raw.execution
      ? {
          jobName: raw.execution.job_name,
          namespace: raw.execution.namespace,
          podName: raw.execution.pod_name,
          nodeName: raw.execution.node_name,
          phase: raw.execution.phase,
          succeeded: raw.execution.succeeded,
        }
      : null,
    candidates: raw.selected_site_id
      ? [
          {
            siteId: raw.selected_site_id,
            nodeName: raw.selected_node_name ?? "unassigned",
            feasible: true,
            computeScore: null,
            opticalScore: null,
            totalScore: raw.optical?.total_score ?? null,
            gsnrDb: raw.optical?.gsnr_db ?? null,
            spectrumN: raw.optical?.n ?? null,
            spectrumM: raw.optical?.m ?? null,
            reason: null,
          },
        ]
      : [],
    events,
  };
}

function mapQueueTask(raw: Record<string, unknown>): QueueTask {
  return {
    taskId: String(raw.task_id),
    tenantId: String(raw.tenant_id),
    priority: Number(raw.priority),
    state: String(raw.state),
    attemptCount: Number(raw.attempt_count),
    notBefore: Number(raw.not_before),
    runtimeState: raw.runtime_state ? String(raw.runtime_state) : null,
    lastError: raw.last_error ? String(raw.last_error) : null,
    createdAt: String(raw.created_at),
  };
}

function mapProfile(raw: Record<string, unknown>): TaskProfile {
  return {
    id: String(raw.profile_id),
    description: String(raw.description),
    defaultCpuM: Number(raw.default_cpu_request_m),
    maxCpuM: Number(raw.max_cpu_request_m),
    defaultMemoryMib: Number(raw.default_memory_request_mib),
    maxMemoryMib: Number(raw.max_memory_request_mib),
    maxGpu: Number(raw.max_gpu_request),
    acceleratorResources: (raw.allowed_accelerator_resource_names as string[]) ?? [],
    acceleratorProducts: (raw.allowed_accelerator_products as string[]) ?? [],
    acceleratorInterconnects: (raw.allowed_accelerator_interconnects as string[]) ?? [],
    defaultBandwidthGbps: Number(raw.default_bandwidth_gbps),
    maxBandwidthGbps: Number(raw.max_bandwidth_gbps),
    defaultDurationSeconds: Number(raw.default_duration_seconds),
    maxDurationSeconds: Number(raw.max_duration_seconds),
    sourceAccessNodes: (raw.allowed_source_access_nodes as string[]) ?? [],
  };
}

function mapSite(raw: Record<string, unknown>): Site {
  const cpu = raw.cpu as Record<string, unknown>;
  const memory = raw.memory as Record<string, unknown>;
  return {
    id: String(raw.site_id),
    name: String(raw.site_name),
    nodeName: String(raw.node_name),
    accessNode: String(raw.network_access_node),
    ready: Boolean(raw.ready),
    schedulable: Boolean(raw.schedulable),
    activePods: Number(raw.active_pod_count),
    cpu: {
      allocatable: Number(cpu.allocatable_m),
      requested: Number(cpu.requested_m),
      available: Number(cpu.available_m),
    },
    memory: {
      allocatable: Number(memory.allocatable_gib) * 1024,
      requested: Number(memory.requested_gib) * 1024,
      available: Number(memory.available_gib) * 1024,
    },
    accelerators: ((raw.accelerators as Record<string, unknown>[]) ?? []).map((item) => ({
      resourceName: String(item.resource_name),
      product: item.product ? String(item.product) : "Unspecified",
      interconnect: item.interconnect ? String(item.interconnect) : "Unspecified",
      partitionMode: item.partition_mode ? String(item.partition_mode) : "Unspecified",
      memoryMib: Number(item.memory_mib ?? 0),
      allocatable: Number(item.allocatable),
      requested: Number(item.requested),
      available: Number(item.available),
    })),
  };
}

export async function loadLiveSnapshot(options: ApiOptions): Promise<Snapshot> {
  const [health, readiness, sitesResponse, tasksResponse, queueResponse, queueStats, profilesResponse] =
    await Promise.all([
      request<Record<string, unknown>>("/healthz", options),
      request<Record<string, unknown>>("/readyz", options),
      request<{ sites: Record<string, unknown>[] }>("/api/v1/sites", options),
      request<{ tasks: RawTask[] }>("/api/v1/tasks", options),
      request<{ tasks: Record<string, unknown>[] }>("/api/v3/queue", options),
      request<Record<string, number>>("/api/v3/queue/stats", options),
      request<{ profiles: Record<string, unknown>[] }>("/api/v2/task-profiles", options),
    ]);

  const queue = queueResponse.tasks.map(mapQueueTask);
  const queueByTask = new Map(queue.map((item) => [item.taskId, item]));
  const tasks = await Promise.all(
    tasksResponse.tasks.map(async (task) => {
      const eventResponse = await request<{ events: Record<string, unknown>[] }>(
        `/api/v2/tasks/${encodeURIComponent(task.task_id)}/events`,
        options,
      );
      return mapTask(task, eventResponse.events.map(mapEvent), queueByTask.get(task.task_id));
    }),
  );

  const spectrum: SpectrumBand[] = tasks.flatMap((task) =>
    task.optical
      ? [
          {
            start: task.optical.n - task.optical.m,
            end: task.optical.n + task.optical.m,
            owner: task.id,
            route: task.optical.route.join(" - "),
            status: task.state === "RELEASED" ? "released" : "active",
          },
        ]
      : [],
  );

  return {
    generatedAt: new Date().toISOString(),
    health: {
      status: health.status === "ok" && readiness.status === "ready" ? "healthy" : "degraded",
      controllerAlive: Boolean(health.controller_alive),
      clusterReady: readiness.status === "ready",
      lastReconciliationAt: health.last_reconciliation_at ? String(health.last_reconciliation_at) : null,
      restoredAllocations: Number(health.restored_allocations ?? 0),
      activeQueue: Number(health.admission_queue_active ?? 0),
    },
    sites: sitesResponse.sites.map(mapSite),
    tasks,
    queue,
    queueStats: {
      queued: Number(queueStats.queued),
      dispatching: Number(queueStats.dispatching),
      active: Number(queueStats.active),
      admitted: Number(queueStats.admitted),
      rejected: Number(queueStats.rejected),
      cancelled: Number(queueStats.cancelled),
    },
    profiles: profilesResponse.profiles.map(mapProfile),
    spectrum,
  };
}

export async function submitLiveTask(
  submission: TaskSubmission,
  options: ApiOptions,
): Promise<void> {
  await request("/api/v3/tasks", options, {
    method: "POST",
    body: JSON.stringify({
      task_id: submission.taskId,
      tenant_id: submission.tenantId,
      priority: submission.priority,
      profile_id: submission.profileId,
      source_access_node: submission.sourceAccessNode,
      cpu_request_m: submission.cpuRequestM,
      memory_request_mib: submission.memoryRequestMib,
      gpu_request: submission.gpuRequest,
      accelerator_resource_name: submission.acceleratorResourceName,
      accelerator_product: submission.acceleratorProduct || null,
      bandwidth_gbps: submission.bandwidthGbps,
      duration_seconds: submission.durationSeconds,
    }),
  });
}
