import { useState, type FormEvent } from "react";
import {
  Activity,
  Eye,
  EyeOff,
  GitBranch,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Network,
  Radio,
  RefreshCw,
  Send,
  ServerCog,
  X,
} from "lucide-react";
import { loadLiveSnapshot, submitLiveTask } from "./lib/api";
import { formatTime } from "./lib/format";
import { replaySnapshot } from "./data/replay";
import type { DataMode, Snapshot, Task, TaskSubmission, ViewId } from "./types";
import {
  DecisionView,
  OverviewView,
  QueueView,
  SubmitView,
  TasksView,
} from "./views/ConsoleViews";

const navItems: Array<{ id: ViewId; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "decision", label: "Decision", icon: GitBranch },
  { id: "queue", label: "Queue", icon: Activity },
  { id: "submit", label: "Submit", icon: Send },
];

const viewTitles: Record<ViewId, { title: string; subtitle: string }> = {
  overview: { title: "Operations overview", subtitle: "Joint compute and optical orchestration" },
  tasks: { title: "Runtime tasks", subtitle: "Persistent state and Kubernetes execution" },
  decision: { title: "Planning decision", subtitle: "Candidate evaluation and committed allocation" },
  queue: { title: "Admission queue", subtitle: "Tenant rotation, priority and dispatch state" },
  submit: { title: "New workload", subtitle: "Profile-controlled task admission" },
};

function createReplayTask(submission: TaskSubmission): Task {
  const createdAt = new Date().toISOString();
  return {
    id: submission.taskId,
    tenantId: submission.tenantId,
    priority: submission.priority,
    profileId: submission.profileId,
    state: "QUEUED",
    version: 1,
    sourceAccessNode: submission.sourceAccessNode,
    selectedSiteId: null,
    selectedNodeName: null,
    cpuRequestM: submission.cpuRequestM,
    memoryRequestMib: submission.memoryRequestMib,
    gpuRequest: submission.gpuRequest,
    acceleratorResourceName: submission.acceleratorResourceName,
    acceleratorProduct: submission.acceleratorProduct || null,
    bandwidthGbps: submission.bandwidthGbps,
    createdAt,
    updatedAt: createdAt,
    failureReason: null,
    optical: null,
    execution: null,
    candidates: [],
    events: [
      {
        id: Date.now(),
        type: "admission.queued",
        previousState: null,
        newState: "QUEUED",
        source: "demo-console",
        message: "Profile-controlled task added to the replay queue.",
        createdAt,
      },
    ],
  };
}

export default function App() {
  const [view, setView] = useState<ViewId>("overview");
  const [mode, setMode] = useState<DataMode>("replay");
  const [snapshot, setSnapshot] = useState<Snapshot>(replaySnapshot);
  const [selectedTaskId, setSelectedTaskId] = useState(replaySnapshot.tasks[0]?.id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [pendingKey, setPendingKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setView("decision");
  };

  const connectLive = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const live = await loadLiveSnapshot({ apiKey: pendingKey });
      setApiKey(pendingKey);
      setSnapshot(live);
      setMode("live");
      setSelectedTaskId(live.tasks[0]?.id ?? "");
      setConnectionOpen(false);
      setMessage("Live runtime connected.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to connect to the runtime.");
    } finally {
      setLoading(false);
    }
  };

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      if (mode === "live") {
        setSnapshot(await loadLiveSnapshot({ apiKey }));
      } else {
        setSnapshot({ ...replaySnapshot, generatedAt: new Date().toISOString() });
      }
      setMessage("Console data refreshed.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Refresh failed.");
    } finally {
      setLoading(false);
    }
  };

  const switchReplay = () => {
    setMode("replay");
    setSnapshot(replaySnapshot);
    setSelectedTaskId(replaySnapshot.tasks[0]?.id ?? "");
    setApiKey("");
    setPendingKey("");
    setError(null);
    setMessage("Replay dataset loaded.");
  };

  const submit = async (submission: TaskSubmission) => {
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "live") {
        await submitLiveTask(submission, { apiKey });
        setSnapshot(await loadLiveSnapshot({ apiKey }));
      } else {
        const task = createReplayTask(submission);
        setSnapshot((current) => ({
          ...current,
          generatedAt: new Date().toISOString(),
          tasks: [task, ...current.tasks],
          queue: [
            {
              taskId: task.id,
              tenantId: task.tenantId,
              priority: task.priority,
              state: "QUEUED",
              attemptCount: 0,
              notBefore: Date.now() / 1000,
              runtimeState: null,
              lastError: null,
              createdAt: task.createdAt,
            },
            ...current.queue,
          ],
          queueStats: {
            ...current.queueStats,
            queued: current.queueStats.queued + 1,
            active: current.queueStats.active + 1,
          },
          health: {
            ...current.health,
            activeQueue: current.health.activeQueue + 1,
          },
        }));
      }
      setSelectedTaskId(submission.taskId);
      setView("queue");
      setMessage(`${submission.taskId} entered the admission queue.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Task submission failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const page = (() => {
    switch (view) {
      case "overview":
        return <OverviewView snapshot={snapshot} onSelectTask={selectTask} />;
      case "tasks":
        return <TasksView snapshot={snapshot} onSelectTask={selectTask} />;
      case "decision":
        return <DecisionView snapshot={snapshot} selectedTaskId={selectedTaskId} onTaskChange={setSelectedTaskId} />;
      case "queue":
        return <QueueView snapshot={snapshot} onSelectTask={selectTask} />;
      case "submit":
        return <SubmitView profiles={snapshot.profiles} onSubmit={submit} submitting={submitting} />;
    }
  })();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => setView("overview")} aria-label="Compute-Optical Console home">
          <span className="brand-mark"><Network size={21} /></span>
          <span><strong>Compute-Optical</strong><small>Orchestrator</small></span>
        </button>
        <nav aria-label="Primary navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} className={view === item.id ? "nav-active" : ""} onClick={() => setView(item.id)} aria-current={view === item.id ? "page" : undefined}><Icon size={18} /><span>{item.label}</span></button>;
          })}
        </nav>
        <div className="sidebar-footer"><span className="runtime-dot" /><div><strong>v0.1.0</strong><small>Research prototype</small></div></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="title-group"><span className="mobile-brand"><Network size={18} />Compute-Optical</span><h1>{viewTitles[view].title}</h1><p>{viewTitles[view].subtitle}</p></div>
          <div className="topbar-actions">
            <div className="mode-control" aria-label="Data source">
              <button className={mode === "replay" ? "mode-active" : ""} onClick={switchReplay}><ServerCog size={14} />Replay</button>
              <button className={mode === "live" ? "mode-active" : ""} onClick={() => setConnectionOpen(true)}><Radio size={14} />Live</button>
            </div>
            <button className="icon-button" onClick={refresh} disabled={loading} title="Refresh data" aria-label="Refresh data"><RefreshCw className={loading ? "spin" : ""} size={18} /></button>
          </div>
        </header>

        <div className="sync-row"><span><span className={`sync-dot sync-${mode}`} />{mode === "live" ? "Live runtime" : "Deterministic replay"}</span><span>Updated {formatTime(snapshot.generatedAt)}</span></div>

        {(message || error) && <div className={`toast ${error ? "toast-error" : "toast-success"}`} role="status"><span>{error ?? message}</span><button onClick={() => { setMessage(null); setError(null); }} aria-label="Dismiss notification"><X size={15} /></button></div>}

        <div className="page-content">{page}</div>
      </main>

      {connectionOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setConnectionOpen(false)}>
          <form className="connection-dialog" onSubmit={connectLive} onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="connection-title">
            <div className="dialog-icon"><KeyRound size={21} /></div>
            <button className="dialog-close" type="button" onClick={() => setConnectionOpen(false)} aria-label="Close"><X size={18} /></button>
            <span className="eyebrow">Same-origin runtime</span><h2 id="connection-title">Live connection</h2>
            <label><span>API endpoint</span><input value={window.location.origin} readOnly /></label>
            <label><span>API key</span><div className="password-field"><input autoFocus minLength={16} required type={showKey ? "text" : "password"} value={pendingKey} onChange={(event) => setPendingKey(event.target.value)} autoComplete="off" /><button type="button" onClick={() => setShowKey((current) => !current)} aria-label={showKey ? "Hide API key" : "Show API key"}>{showKey ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>
            {error && <div className="dialog-error">{error}</div>}
            <button className="button button-primary" type="submit" disabled={loading || pendingKey.length < 16}>{loading ? "Connecting..." : "Connect"}</button>
          </form>
        </div>
      )}
    </div>
  );
}
