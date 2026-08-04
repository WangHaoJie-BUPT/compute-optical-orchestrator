import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useEffect, useState } from "react";
import "@xyflow/react/dist/style.css";
import type { Site, Task } from "../types";

interface Props {
  sites: Site[];
  task: Task;
}

export function TopologyGraph({ sites, task }: Props) {
  const [compact, setCompact] = useState(() =>
    window.matchMedia("(max-width: 560px)").matches,
  );

  useEffect(() => {
    const media = window.matchMedia("(max-width: 560px)");
    const updateLayout = () => setCompact(media.matches);
    media.addEventListener("change", updateLayout);
    return () => media.removeEventListener("change", updateLayout);
  }, []);

  const sourcePosition = compact
    ? { x: 10, y: 20 }
    : { x: 20, y: 126 };
  const corePosition = compact
    ? { x: 170, y: 20 }
    : { x: 265, y: 126 };
  const compactSitePositions = [
    { x: 10, y: 150 },
    { x: 170, y: 150 },
    { x: 90, y: 235 },
  ];

  const nodes: Node[] = [
    {
      id: "source",
      position: sourcePosition,
      data: { label: task.sourceAccessNode.replace("trx ", "") },
      className: "topology-node topology-source",
    },
    {
      id: "core",
      position: corePosition,
      data: { label: "Optical core" },
      className: "topology-node topology-core",
    },
    ...sites.map((site, index) => ({
      id: site.id,
      position: compact
        ? compactSitePositions[index]
        : { x: 535, y: 22 + index * 104 },
      data: {
        label: `${site.name}\n${site.nodeName.replace("compute-optical-", "")}`,
      },
      className: `topology-node topology-site ${
        site.id === task.selectedSiteId ? "topology-selected" : ""
      }`,
    })),
  ];

  const selected = task.selectedSiteId;
  const edges: Edge[] = [
    {
      id: "source-core",
      source: "source",
      target: "core",
      animated: Boolean(selected),
      markerEnd: { type: MarkerType.ArrowClosed },
      className: selected ? "topology-edge-selected" : "",
    },
    ...sites.map((site) => ({
      id: `core-${site.id}`,
      source: "core",
      target: site.id,
      animated: site.id === selected,
      markerEnd: { type: MarkerType.ArrowClosed },
      className: site.id === selected ? "topology-edge-selected" : "",
      label:
        site.id === selected && task.optical
          ? `${task.optical.gsnrDb.toFixed(1)} dB`
          : undefined,
    })),
  ];

  return (
    <div className="topology-canvas" aria-label="Compute and optical topology">
      <ReactFlow
        key={compact ? "compact" : "wide"}
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.14 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll={false}
        minZoom={0.75}
        maxZoom={1.25}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="#dce3df" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
