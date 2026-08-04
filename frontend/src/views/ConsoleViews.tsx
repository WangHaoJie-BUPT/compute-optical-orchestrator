import { lazy, Suspense, useMemo, useState, type FormEvent } from "react";
import {
  Activity,
  AlertTriangle,
  Cable,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  Gauge,
  MemoryStick,
  Network,
  Search,
  Server,
  TimerReset,
  XCircle,
} from "lucide-react";
import { formatDateTime, formatDuration, formatNumber, formatTime } from "../lib/format";
import type { Snapshot, Task, TaskProfile, TaskSubmission } from "../types";
import { StatusPill } from "../components/StatusPill";

const ActivityChart = lazy(() =>
  import("../components/ActivityChart").then((module) => ({ default: module.ActivityChart })),
);
const SpectrumMap = lazy(() =>
  import("../components/SpectrumMap").then((module) => ({ default: module.SpectrumMap })),
);
const TopologyGraph = lazy(() =>
  import("../components/TopologyGraph").then((module) => ({ default: module.TopologyGraph })),
);

interface SharedProps {
  snapshot: Snapshot;
  onSelectTask: (taskId: string) => void;
}

function Metric({ label, value, detail, icon }: { label: string; value: string; detail: string; icon: React.ReactNode }) {
  return (
    <div className="metric-card">
      <div className="metric-icon">{icon}</div>
      <div>
        <span className="metric-label">{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function UtilizationBar({ value, tone = "green" }: { value: number; tone?: "green" | "blue" | "amber" }) {
  return (
    <div className="utilization-track" aria-label={`${Math.round(value)} percent utilized`}>
      <span className={`utilization-fill utilization-${tone}`} style={{ width: `${Math.min(100, value)}%` }} />
    </div>
  );
}

export function OverviewView({ snapshot, onSelectTask }: SharedProps) {
  const activeTasks = snapshot.tasks.filter((task) => ["RUNNING", "PLANNING", "RESERVED", "SUBMITTING"].includes(task.state)).length;
  const allocatedGbps = snapshot.tasks.reduce((sum, task) => sum + (task.optical && task.state !== "RELEASED" ? task.bandwidthGbps : 0), 0);
  const freeAccelerators = snapshot.sites.reduce((sum, site) => sum + site.accelerators.reduce((inner, item) => inner + item.available, 0), 0);

  return (
    <div className="view-stack">
      <section className="metric-grid" aria-label="System metrics">
        <Metric label="Active tasks" value={String(activeTasks)} detail={`${snapshot.tasks.length} persisted`} icon={<Activity size={20} />} />
        <Metric label="Admission queue" value={String(snapshot.queueStats.active)} detail={`${snapshot.queueStats.admitted} admitted`} icon={<TimerReset size={20} />} />
        <Metric label="Optical demand" value={`${formatNumber(allocatedGbps)} Gb/s`} detail={`${snapshot.spectrum.filter((item) => item.status === "active").length} active claims`} icon={<Cable size={20} />} />
        <Metric label="Accelerators free" value={String(freeAccelerators)} detail="Across three sites" icon={<Cpu size={20} />} />
      </section>

      <section className="content-grid overview-grid">
        <div className="section-block activity-block">
          <div className="section-heading">
            <div>
              <span className="eyebrow">Runtime activity</span>
              <h2>Orchestration throughput</h2>
            </div>
            <span className="period-label">Last 5 hours</span>
          </div>
          <div className="chart-frame">
            <Suspense fallback={<div className="visual-loading">Loading chart</div>}>
              <ActivityChart />
            </Suspense>
          </div>
          <div className="chart-legend"><span><i className="legend-green" />Admitted</span><span><i className="legend-blue" />Completed</span></div>
        </div>

        <div className="section-block health-block">
          <div className="section-heading">
            <div><span className="eyebrow">Control plane</span><h2>System health</h2></div>
            <StatusPill state={snapshot.health.status} />
          </div>
          <dl className="health-list">
            <div><dt><Server size={16} />Kubernetes</dt><dd>{snapshot.health.clusterReady ? "Ready" : "Unavailable"}</dd></div>
            <div><dt><Activity size={16} />Controller</dt><dd>{snapshot.health.controllerAlive ? "Running" : "Stopped"}</dd></div>
            <div><dt><Database size={16} />Restored claims</dt><dd>{snapshot.health.restoredAllocations}</dd></div>
            <div><dt><Clock3 size={16} />Last reconcile</dt><dd>{formatTime(snapshot.health.lastReconciliationAt)}</dd></div>
          </dl>
        </div>
      </section>

      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">Compute fabric</span><h2>Site capacity</h2></div><span className="period-label">Live allocatable minus requests</span></div>
        <div className="site-grid">
          {snapshot.sites.map((site) => {
            const cpuUsed = (site.cpu.requested / site.cpu.allocatable) * 100;
            const memoryUsed = (site.memory.requested / site.memory.allocatable) * 100;
            return (
              <article className="site-card" key={site.id}>
                <div className="site-title"><div><span className={`site-dot ${site.ready ? "site-ready" : "site-down"}`} /><strong>{site.name}</strong></div><small>{site.nodeName}</small></div>
                <div className="resource-row"><span><Cpu size={14} />CPU</span><strong>{formatNumber(site.cpu.available / 1000, 1)} cores free</strong></div>
                <UtilizationBar value={cpuUsed} />
                <div className="resource-row"><span><MemoryStick size={14} />Memory</span><strong>{formatNumber(site.memory.available / 1024, 1)} GiB free</strong></div>
                <UtilizationBar value={memoryUsed} tone="blue" />
                <div className="accelerator-line">{site.accelerators.map((item) => <span key={item.resourceName}>{item.product}<b>{item.available}/{item.allocatable}</b></span>)}</div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">Recent work</span><h2>Task activity</h2></div></div>
        <TaskTable tasks={snapshot.tasks.slice(0, 5)} onSelectTask={onSelectTask} />
      </section>
    </div>
  );
}

function TaskTable({ tasks, onSelectTask }: { tasks: Task[]; onSelectTask: (taskId: string) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Task</th><th>State</th><th>Tenant</th><th>Placement</th><th>Resources</th><th>Updated</th></tr></thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.id} onClick={() => onSelectTask(task.id)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") onSelectTask(task.id); }}>
              <td><strong>{task.id}</strong><small>{task.profileId}</small></td>
              <td><StatusPill state={task.state} /></td>
              <td>{task.tenantId}</td>
              <td>{task.selectedSiteId?.replace("site-", "") ?? "Unassigned"}</td>
              <td>{task.cpuRequestM}m / {task.memoryRequestMib}Mi / {task.gpuRequest} acc.</td>
              <td>{formatTime(task.updatedAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TasksView({ snapshot, onSelectTask }: SharedProps) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState("ALL");
  const states = ["ALL", ...Array.from(new Set(snapshot.tasks.map((task) => task.state)))];
  const tasks = snapshot.tasks.filter((task) => {
    const matchesText = `${task.id} ${task.tenantId} ${task.selectedSiteId}`.toLowerCase().includes(query.toLowerCase());
    return matchesText && (state === "ALL" || task.state === state);
  });
  return (
    <div className="view-stack">
      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">Persistent runtime</span><h2>Tasks</h2></div><span className="count-label">{tasks.length} records</span></div>
        <div className="filter-bar">
          <label className="search-field"><Search size={16} /><input aria-label="Search tasks" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, tenant or site" /></label>
          <select aria-label="Filter by task state" value={state} onChange={(event) => setState(event.target.value)}>{states.map((item) => <option value={item} key={item}>{item}</option>)}</select>
        </div>
        <TaskTable tasks={tasks} onSelectTask={onSelectTask} />
      </section>
    </div>
  );
}

export function DecisionView({ snapshot, selectedTaskId, onTaskChange }: { snapshot: Snapshot; selectedTaskId: string; onTaskChange: (taskId: string) => void }) {
  const task = snapshot.tasks.find((item) => item.id === selectedTaskId) ?? snapshot.tasks[0];
  if (!task) return <div className="empty-state">No task decisions are available.</div>;
  const taskBands = snapshot.spectrum.filter((band) => band.owner === task.id || band.status === "active");
  return (
    <div className="view-stack">
      <section className="decision-header">
        <div><span className="eyebrow">Joint planning decision</span><h2>{task.id}</h2><div className="inline-meta"><StatusPill state={task.state} /><span>v{task.version}</span><span>{formatDuration(task.createdAt, task.updatedAt)}</span></div></div>
        <label className="task-select"><span>Task record</span><select value={task.id} onChange={(event) => onTaskChange(event.target.value)}>{snapshot.tasks.map((item) => <option value={item.id} key={item.id}>{item.id}</option>)}</select></label>
      </section>

      {task.failureReason && <div className="alert-strip"><AlertTriangle size={17} /><span>{task.failureReason}</span></div>}

      <section className="content-grid decision-grid">
        <div className="section-block topology-block"><div className="section-heading"><div><span className="eyebrow">Selected path</span><h2>Compute-optical topology</h2></div>{task.optical && <span className="score-label">Score {task.optical.totalScore.toFixed(1)}</span>}</div><Suspense fallback={<div className="topology-canvas visual-loading">Loading topology</div>}><TopologyGraph sites={snapshot.sites} task={task} /></Suspense></div>
        <div className="section-block allocation-block"><div className="section-heading"><div><span className="eyebrow">Allocation</span><h2>Committed resources</h2></div></div>
          <dl className="allocation-list">
            <div><dt><Server size={15} />Compute</dt><dd>{task.selectedNodeName ?? "Not allocated"}</dd></div>
            <div><dt><Cpu size={15} />Accelerator</dt><dd>{task.gpuRequest ? `${task.gpuRequest} x ${task.acceleratorProduct ?? task.acceleratorResourceName}` : "CPU workload"}</dd></div>
            <div><dt><Cable size={15} />Spectrum</dt><dd>{task.optical ? `N ${task.optical.n} / M ${task.optical.m}` : "Not allocated"}</dd></div>
            <div><dt><Gauge size={15} />GSNR</dt><dd>{task.optical ? `${task.optical.gsnrDb.toFixed(1)} dB` : "Not computed"}</dd></div>
          </dl>
        </div>
      </section>

      <section className="section-block"><div className="section-heading"><div><span className="eyebrow">Candidate evaluation</span><h2>Placement comparison</h2></div></div>
        <div className="candidate-grid">
          {(task.candidates.length ? task.candidates : snapshot.sites.map((site) => ({ siteId: site.id, nodeName: site.nodeName, feasible: site.id === task.selectedSiteId, computeScore: null, opticalScore: null, totalScore: site.id === task.selectedSiteId ? task.optical?.totalScore ?? null : null, gsnrDb: site.id === task.selectedSiteId ? task.optical?.gsnrDb ?? null : null, spectrumN: null, spectrumM: null, reason: site.id === task.selectedSiteId ? null : "Not selected by the persisted plan." }))).map((candidate) => (
            <article className={`candidate-card ${candidate.siteId === task.selectedSiteId ? "candidate-selected" : ""}`} key={candidate.siteId}>
              <div className="candidate-title"><div>{candidate.feasible ? <CheckCircle2 size={17} /> : <XCircle size={17} />}<strong>{candidate.siteId.replace("site-", "")}</strong></div>{candidate.totalScore !== null && <b>{candidate.totalScore.toFixed(1)}</b>}</div>
              <span>{candidate.nodeName}</span>
              {candidate.feasible ? <dl><div><dt>Compute</dt><dd>{candidate.computeScore?.toFixed(1) ?? "Persisted"}</dd></div><div><dt>Optical</dt><dd>{candidate.opticalScore?.toFixed(1) ?? `${candidate.gsnrDb?.toFixed(1)} dB`}</dd></div></dl> : <p>{candidate.reason}</p>}
            </article>
          ))}
        </div>
      </section>

      <section className="section-block"><div className="section-heading"><div><span className="eyebrow">Flex-grid</span><h2>Spectrum occupancy</h2></div></div><Suspense fallback={<div className="visual-loading">Loading spectrum</div>}><SpectrumMap bands={taskBands} /></Suspense></section>

      <section className="section-block"><div className="section-heading"><div><span className="eyebrow">Immutable audit</span><h2>Lifecycle timeline</h2></div></div>
        <ol className="timeline">
          {task.events.map((event, index) => <li key={event.id}><span className={`timeline-marker ${index === task.events.length - 1 ? "timeline-current" : ""}`}>{index + 1}</span><div><div className="timeline-title"><strong>{event.type.replaceAll(".", " ")}</strong><time>{formatDateTime(event.createdAt)}</time></div><p>{event.message}</p><small>{event.source}{event.newState ? ` -> ${event.newState}` : ""}</small></div></li>)}
        </ol>
      </section>
    </div>
  );
}

export function QueueView({ snapshot, onSelectTask }: SharedProps) {
  const stats = snapshot.queueStats;
  return (
    <div className="view-stack">
      <section className="metric-grid queue-metrics">
        <Metric label="Queued" value={String(stats.queued)} detail="Awaiting tenant turn" icon={<Clock3 size={20} />} />
        <Metric label="Dispatching" value={String(stats.dispatching)} detail="Claimed by controller" icon={<Activity size={20} />} />
        <Metric label="Admitted" value={String(stats.admitted)} detail="Runtime submission started" icon={<CheckCircle2 size={20} />} />
        <Metric label="Rejected" value={String(stats.rejected)} detail={`${stats.cancelled} cancelled`} icon={<XCircle size={20} />} />
      </section>
      <section className="section-block"><div className="section-heading"><div><span className="eyebrow">Durable admission</span><h2>Tenant rotation</h2></div><span className="count-label">{snapshot.queue.length} records</span></div>
        <div className="table-wrap"><table><thead><tr><th>Task</th><th>Tenant</th><th>Priority</th><th>Admission</th><th>Runtime</th><th>Attempts</th><th>Created</th></tr></thead><tbody>{snapshot.queue.map((item) => <tr key={item.taskId} onClick={() => onSelectTask(item.taskId)}><td><strong>{item.taskId}</strong></td><td>{item.tenantId}</td><td><span className="priority-value">{item.priority}</span></td><td><StatusPill state={item.state} /></td><td>{item.runtimeState ? <StatusPill state={item.runtimeState} /> : "-"}</td><td>{item.attemptCount}</td><td>{formatTime(item.createdAt)}</td></tr>)}</tbody></table></div>
      </section>
    </div>
  );
}

interface SubmitProps {
  profiles: TaskProfile[];
  onSubmit: (submission: TaskSubmission) => Promise<void>;
  submitting: boolean;
}

export function SubmitView({ profiles, onSubmit, submitting }: SubmitProps) {
  const profile = profiles[0];
  const [form, setForm] = useState(() => ({
    taskId: `demo-task-${Math.floor(Date.now() / 1000).toString().slice(-5)}`,
    tenantId: "demo-lab",
    priority: 60,
    profileId: profile?.id ?? "busybox-smoke",
    sourceAccessNode: profile?.sourceAccessNodes[0] ?? "trx Lorient_KMA",
    cpuRequestM: profile?.defaultCpuM ?? 500,
    memoryRequestMib: profile?.defaultMemoryMib ?? 256,
    gpuRequest: 0,
    acceleratorResourceName: profile?.acceleratorResources[0] ?? "nvidia.com/gpu",
    acceleratorProduct: "",
    bandwidthGbps: profile?.defaultBandwidthGbps ?? 100,
    durationSeconds: profile?.defaultDurationSeconds ?? 10,
  }));
  const selectedProfile = useMemo(() => profiles.find((item) => item.id === form.profileId) ?? profile, [form.profileId, profile, profiles]);
  const update = (key: keyof typeof form, value: string | number) => setForm((current) => ({ ...current, [key]: value }));
  const handleSubmit = async (event: FormEvent) => { event.preventDefault(); await onSubmit(form); };
  if (!selectedProfile) return <div className="empty-state">No controlled task profile is available.</div>;

  return (
    <div className="submit-layout">
      <form className="section-block submit-form" onSubmit={handleSubmit}>
        <div className="section-heading"><div><span className="eyebrow">Controlled admission</span><h2>Submit task</h2></div><StatusPill state="profile controlled" /></div>
        <div className="form-grid">
          <label><span>Task ID</span><input required pattern="[a-z0-9]([-a-z0-9.]*[a-z0-9])?" value={form.taskId} onChange={(event) => update("taskId", event.target.value)} /></label>
          <label><span>Tenant</span><input required value={form.tenantId} onChange={(event) => update("tenantId", event.target.value)} /></label>
          <label><span>Task profile</span><select value={form.profileId} onChange={(event) => update("profileId", event.target.value)}>{profiles.map((item) => <option value={item.id} key={item.id}>{item.id}</option>)}</select></label>
          <label><span>Source access node</span><select value={form.sourceAccessNode} onChange={(event) => update("sourceAccessNode", event.target.value)}>{selectedProfile.sourceAccessNodes.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
          <label><span>CPU request (m)</span><input type="number" min="1" max={selectedProfile.maxCpuM} value={form.cpuRequestM} onChange={(event) => update("cpuRequestM", Number(event.target.value))} /></label>
          <label><span>Memory (MiB)</span><input type="number" min="1" max={selectedProfile.maxMemoryMib} value={form.memoryRequestMib} onChange={(event) => update("memoryRequestMib", Number(event.target.value))} /></label>
          <label><span>Accelerators</span><input type="number" min="0" max={selectedProfile.maxGpu} value={form.gpuRequest} onChange={(event) => update("gpuRequest", Number(event.target.value))} /></label>
          <label><span>Resource name</span><select value={form.acceleratorResourceName} disabled={form.gpuRequest === 0} onChange={(event) => update("acceleratorResourceName", event.target.value)}>{selectedProfile.acceleratorResources.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
          <label><span>Product constraint</span><select value={form.acceleratorProduct} disabled={form.gpuRequest === 0} onChange={(event) => update("acceleratorProduct", event.target.value)}><option value="">Any allowed</option>{selectedProfile.acceleratorProducts.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
          <label><span>Bandwidth (Gb/s)</span><input type="number" min="1" max={selectedProfile.maxBandwidthGbps} value={form.bandwidthGbps} onChange={(event) => update("bandwidthGbps", Number(event.target.value))} /></label>
          <label><span>Duration (seconds)</span><input type="number" min="1" max={selectedProfile.maxDurationSeconds} value={form.durationSeconds} onChange={(event) => update("durationSeconds", Number(event.target.value))} /></label>
          <label><span>Priority</span><input type="number" min="0" max="100" value={form.priority} onChange={(event) => update("priority", Number(event.target.value))} /></label>
        </div>
        <div className="submit-actions"><button className="button button-primary" type="submit" disabled={submitting}>{submitting ? "Submitting..." : "Submit to admission queue"}</button></div>
      </form>
      <aside className="submission-summary">
        <span className="eyebrow">Request envelope</span><h2>{selectedProfile.id}</h2><p>{selectedProfile.description}</p>
        <dl><div><dt>Compute</dt><dd>{form.cpuRequestM}m / {form.memoryRequestMib}Mi</dd></div><div><dt>Accelerator</dt><dd>{form.gpuRequest || "None"}</dd></div><div><dt>Optical</dt><dd>{form.bandwidthGbps} Gb/s</dd></div><div><dt>Tenant priority</dt><dd>{form.tenantId} / {form.priority}</dd></div></dl>
        <div className="policy-check"><CheckCircle2 size={16} /><span>Profile allowlist</span></div><div className="policy-check"><CheckCircle2 size={16} /><span>Server-side command</span></div><div className="policy-check"><CheckCircle2 size={16} /><span>Durable admission</span></div>
      </aside>
    </div>
  );
}
