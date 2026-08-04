export function formatNumber(value: number, digits = 0): string {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatTime(value: string | null): string {
  if (!value) return "Not recorded";
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatDuration(from: string, to: string): string {
  const seconds = Math.max(
    0,
    Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function stateTone(state: string): string {
  const normalized = state.toUpperCase();
  if (["RUNNING", "ADMITTED", "COMPLETED", "RELEASED"].includes(normalized)) {
    return "success";
  }
  if (["PLANNING", "RESERVED", "SUBMITTING", "DISPATCHING"].includes(normalized)) {
    return "info";
  }
  if (["QUEUED", "PENDING", "RELEASING"].includes(normalized)) {
    return "warning";
  }
  if (["FAILED", "BLOCKED", "REJECTED", "RELEASE_FAILED"].includes(normalized)) {
    return "danger";
  }
  return "neutral";
}
