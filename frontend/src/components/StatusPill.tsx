import { Circle } from "lucide-react";
import { stateTone } from "../lib/format";

export function StatusPill({ state }: { state: string }) {
  return (
    <span className={`status-pill status-${stateTone(state)}`}>
      <Circle aria-hidden="true" size={7} fill="currentColor" />
      {state.replaceAll("_", " ")}
    </span>
  );
}
