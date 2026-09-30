// Pure view-derivation logic (VIZ-05): search, group/type filters,
// 1-hop focus, and highlight dimming. Framework-free so it stays testable;
// the canvas only applies the resulting id sets.
import type { VizPayload } from "./viz-types";
import { groupKey, typeKey } from "./graph";

export interface ViewOptions {
  query: string;
  hiddenGroups: ReadonlySet<string>;
  hiddenTypes: ReadonlySet<string>;
  focusNodeId: string | null;
  highlightOn: boolean;
  searchProperties?: boolean;
  requiredLabel?: string;
  focusDirection?: "in" | "out" | "both";
  focusHops?: number;
}

export interface ViewSets {
  hiddenNodes: string[];
  hiddenEdges: string[];
  dimNodes: string[];
  dimEdges: string[];
  visibleNodeCount: number;
  visibleEdgeCount: number;
}

export function neighborMap(payload: VizPayload, direction: "in" | "out" | "both" = "both",
  hiddenTypes: ReadonlySet<string> = new Set(), excludedNodes: ReadonlySet<string> = new Set()): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    if (!map.has(a)) map.set(a, new Set());
    map.get(a)!.add(b);
  };
  for (const edge of payload.edges) {
    if (hiddenTypes.has(typeKey(edge)) || excludedNodes.has(edge.source) || excludedNodes.has(edge.target)) continue;
    if (direction !== "in") link(edge.source, edge.target);
    if (direction !== "out") link(edge.target, edge.source);
  }
  return map;
}

export function computeView(payload: VizPayload, options: ViewOptions): ViewSets {
  const query = options.query.trim().toLowerCase();
  const nodeById = new Map(payload.nodes.map((node) => [node.id, node]));
  const excluded = new Set(payload.nodes.filter(node => options.hiddenGroups.has(groupKey(node)) ||
    (options.requiredLabel && !node.labels.includes(options.requiredLabel))).map(node => node.id));
  const neighbors = neighborMap(payload, options.focusDirection, options.hiddenTypes, excluded);

  let focusSet: Set<string> | null = null;
  if (options.focusNodeId !== null) {
    focusSet = new Set(excluded.has(options.focusNodeId) ? [] : [options.focusNodeId]);
    let frontier = [...focusSet];
    for (let hop = 0; hop < (options.focusHops ?? 1); hop++) {
      const next: string[] = [];
      for (const id of frontier) for (const neighbor of neighbors.get(id) ?? []) {
        if (!focusSet.has(neighbor)) { focusSet.add(neighbor); next.push(neighbor); }
      }
      frontier = next;
      if (!frontier.length) break;
    }
  }

  const hiddenNodes = new Set<string>();
  for (const node of payload.nodes) {
    if (focusSet !== null && !focusSet.has(node.id)) {
      hiddenNodes.add(node.id);
      continue;
    }
    if (excluded.has(node.id)) {
      hiddenNodes.add(node.id);
      continue;
    }
    if (query) {
      const properties = options.searchProperties ? JSON.stringify({ ...node.properties, ...node.extra }) : "";
      const haystack = `${node.id} ${node.labels.join(" ")} ${properties}`.toLowerCase();
      if (!haystack.includes(query)) hiddenNodes.add(node.id);
    }
  }

  const hiddenEdges = new Set<string>();
  for (const edge of payload.edges) {
    if (options.hiddenTypes.has(typeKey(edge))) {
      hiddenEdges.add(edge.id);
      continue;
    }
    if (hiddenNodes.has(edge.source) || hiddenNodes.has(edge.target)) {
      hiddenEdges.add(edge.id);
    }
  }

  const highlightNodes = new Set(payload.highlight.nodes.filter((id) => nodeById.has(id)));
  const highlightEdges = new Set(payload.highlight.edges);
  const dimNodes = new Set<string>();
  const dimEdges = new Set<string>();
  if (options.highlightOn && (highlightNodes.size > 0 || highlightEdges.size > 0)) {
    for (const node of payload.nodes) {
      if (!hiddenNodes.has(node.id) && !highlightNodes.has(node.id)) dimNodes.add(node.id);
    }
    for (const edge of payload.edges) {
      if (!hiddenEdges.has(edge.id) && !highlightEdges.has(edge.id)) dimEdges.add(edge.id);
    }
  }

  return {
    hiddenNodes: [...hiddenNodes].sort(),
    hiddenEdges: [...hiddenEdges].sort(),
    dimNodes: [...dimNodes].sort(),
    dimEdges: [...dimEdges].sort(),
    visibleNodeCount: payload.nodes.length - hiddenNodes.size,
    visibleEdgeCount: payload.edges.length - hiddenEdges.size,
  };
}
