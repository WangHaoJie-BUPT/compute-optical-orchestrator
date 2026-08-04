export type DataMode = "replay" | "live";

export type ViewId =
  | "overview"
  | "tasks"
  | "decision"
  | "queue"
  | "submit";

export type HealthStatus = "healthy" | "degraded" | "offline";

export interface HealthSummary {
  status: HealthStatus;
  controllerAlive: boolean;
  clusterReady: boolean;
  lastReconciliationAt: string | null;
  restoredAllocations: number;
  activeQueue: number;
}

export interface Accelerator {
  resourceName: string;
  product: string;
  interconnect: string;
  partitionMode: string;
  memoryMib: number;
  allocatable: number;
  requested: number;
  available: number;
}

export interface Site {
  id: string;
  name: string;
  nodeName: string;
  accessNode: string;
  ready: boolean;
  schedulable: boolean;
  activePods: number;
  cpu: { allocatable: number; requested: number; available: number };
  memory: { allocatable: number; requested: number; available: number };
  accelerators: Accelerator[];
}

export interface Candidate {
  siteId: string;
  nodeName: string;
  feasible: boolean;
  computeScore: number | null;
  opticalScore: number | null;
  totalScore: number | null;
  gsnrDb: number | null;
  spectrumN: number | null;
  spectrumM: number | null;
  reason: string | null;
}

export interface TaskEvent {
  id: number;
  type: string;
  previousState: string | null;
  newState: string | null;
  source: string;
  message: string;
  createdAt: string;
}

export interface OpticalAllocation {
  siteId: string;
  accessNode: string;
  n: number;
  m: number;
  route: string[];
  fiberLinks: string[];
  gsnrDb: number;
  totalScore: number;
}

export interface Execution {
  jobName: string;
  namespace: string;
  podName: string | null;
  nodeName: string | null;
  phase: string;
  succeeded: boolean | null;
}

export interface Task {
  id: string;
  tenantId: string;
  priority: number;
  profileId: string;
  state: string;
  version: number;
  sourceAccessNode: string;
  selectedSiteId: string | null;
  selectedNodeName: string | null;
  cpuRequestM: number;
  memoryRequestMib: number;
  gpuRequest: number;
  acceleratorResourceName: string;
  acceleratorProduct: string | null;
  bandwidthGbps: number;
  createdAt: string;
  updatedAt: string;
  failureReason: string | null;
  optical: OpticalAllocation | null;
  execution: Execution | null;
  candidates: Candidate[];
  events: TaskEvent[];
}

export interface QueueTask {
  taskId: string;
  tenantId: string;
  priority: number;
  state: string;
  attemptCount: number;
  notBefore: number;
  runtimeState: string | null;
  lastError: string | null;
  createdAt: string;
}

export interface QueueStats {
  queued: number;
  dispatching: number;
  active: number;
  admitted: number;
  rejected: number;
  cancelled: number;
}

export interface TaskProfile {
  id: string;
  description: string;
  defaultCpuM: number;
  maxCpuM: number;
  defaultMemoryMib: number;
  maxMemoryMib: number;
  maxGpu: number;
  acceleratorResources: string[];
  acceleratorProducts: string[];
  acceleratorInterconnects: string[];
  defaultBandwidthGbps: number;
  maxBandwidthGbps: number;
  defaultDurationSeconds: number;
  maxDurationSeconds: number;
  sourceAccessNodes: string[];
}

export interface SpectrumBand {
  start: number;
  end: number;
  owner: string;
  route: string;
  status: "active" | "released" | "reserved";
}

export interface Snapshot {
  generatedAt: string;
  health: HealthSummary;
  sites: Site[];
  tasks: Task[];
  queue: QueueTask[];
  queueStats: QueueStats;
  profiles: TaskProfile[];
  spectrum: SpectrumBand[];
}

export interface TaskSubmission {
  taskId: string;
  tenantId: string;
  priority: number;
  profileId: string;
  sourceAccessNode: string;
  cpuRequestM: number;
  memoryRequestMib: number;
  gpuRequest: number;
  acceleratorResourceName: string;
  acceleratorProduct?: string;
  bandwidthGbps: number;
  durationSeconds: number;
}
