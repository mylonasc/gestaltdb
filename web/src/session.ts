/** Versioned, validated local view state; graph data remains a separate artifact. */
import type { VizPayload, ViewerSettings } from "./viz-types";
import type { Selection } from "./Inspector";

export interface LayoutState {
  nodes: { id: string; x: number; y: number; pinned: boolean }[];
  zoom: { x: number; y: number; k: number };
  simulation: { nodes: string[]; edges: string[] };
}

export interface ViewState {
  version: 1;
  kind: "gestaltdb-view";
  graph: { nodes: string[]; edges: string[][] };
  layout: LayoutState;
  settings: ViewerSettings;
  filters: {
    query: string; hiddenGroups: string[]; hiddenTypes: string[]; requiredLabel: string;
    focusNodeId: string | null; focusDirection: "in" | "out" | "both"; focusHops: number;
    searchProperties: boolean; highlightOn: boolean;
  };
  selection: Selection | null;
}

export function graphIdentity(payload: VizPayload): ViewState["graph"] {
  return { nodes: payload.nodes.map(node => node.id).sort(),
    edges: payload.edges.map(edge => [edge.id, edge.source, edge.target]).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0) };
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected a view-state object");
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("Expected a view-state string");
  return value;
}
function flag(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error("Expected a view-state boolean");
  return value;
}
function finite(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > 1e12) throw new Error("View coordinates and settings must be finite numbers within ±1e12");
  return value;
}
function choice<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (!allowed.includes(value as T)) throw new Error(`Expected one of ${allowed.join(", ")}`);
  return value as T;
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error("Expected a view-state list");
  return value.map(text);
}
function property(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (!text(value)) throw new Error("Property names must be nonempty strings");
  return text(value);
}

export function parseViewState(value: unknown, payload: VizPayload): ViewState {
  const state = record(value);
  if (state.version !== 1 || state.kind !== "gestaltdb-view") throw new Error("Unsupported view-state format");
  const identity = graphIdentity(payload);
  const graph = record(state.graph);
  if (!Array.isArray(graph.edges)) throw new Error("View state has no edge identity");
  const edges = graph.edges.map(edge => {
    const fields = strings(edge);
    if (fields.length !== 3) throw new Error("Invalid view-state edge identity");
    return fields;
  }).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
  if (JSON.stringify({ nodes: strings(graph.nodes).sort(), edges }) !== JSON.stringify(identity)) throw new Error("View state belongs to a different graph topology");
  const layout = record(state.layout), zoom = record(layout.zoom);
  if (!Array.isArray(layout.nodes)) throw new Error("View state has no node coordinates");
  const ids = new Set(identity.nodes);
  const nodes = layout.nodes.map(raw => {
    const node = record(raw), id = text(node.id);
    if (!ids.delete(id)) throw new Error("Unknown or duplicate node in view state");
    return { id, x: finite(node.x), y: finite(node.y), pinned: flag(node.pinned) };
  });
  if (ids.size) throw new Error("View state is missing node coordinates");
  const k = finite(zoom.k);
  if (k < 0.1 || k > 8) throw new Error("View zoom must be between 0.1 and 8");
  const active = layout.simulation === undefined ? { nodes: identity.nodes, edges: identity.edges.map(edge => edge[0]) } : record(layout.simulation);
  const activeNodes = strings(active.nodes), activeEdges = strings(active.edges);
  const knownNodes = new Set(identity.nodes), activeSet = new Set(activeNodes);
  const knownEdges = new Map(identity.edges.map(edge => [edge[0], edge]));
  if (activeSet.size !== activeNodes.length || activeNodes.some(id => !knownNodes.has(id)) ||
      new Set(activeEdges).size !== activeEdges.length || activeEdges.some(id => {
        const edge = knownEdges.get(id);
        return !edge || !activeSet.has(edge[1]) || !activeSet.has(edge[2]);
      })) throw new Error("Invalid active simulation topology");
  const settings = record(state.settings), filters = record(state.filters);
  const labelMaxLength = finite(settings.labelMaxLength);
  const focusHops = finite(filters.focusHops);
  if (!Number.isInteger(labelMaxLength) || labelMaxLength < 1) throw new Error("Invalid label length");
  if (!Number.isInteger(focusHops) || focusHops < 1 || focusHops > 5) throw new Error("Neighborhood hops must be 1–5");
  const focusNodeId = filters.focusNodeId === null ? null : text(filters.focusNodeId);
  if (focusNodeId !== null && !identity.nodes.includes(focusNodeId)) throw new Error("Unknown focus node");
  let selection: Selection | null = null;
  if (state.selection !== null) {
    const item = record(state.selection);
    const kind = choice(item.kind, ["node", "edge"]), id = text(item.id);
    if (!(kind === "node" ? identity.nodes.includes(id) : identity.edges.some(edge => edge[0] === id))) throw new Error("Unknown selected entity");
    selection = { kind, id };
  }
  return {
    version: 1, kind: "gestaltdb-view", graph: identity,
    layout: { nodes, zoom: { x: finite(zoom.x), y: finite(zoom.y), k }, simulation: { nodes: activeNodes, edges: activeEdges } }, selection,
    settings: {
      paused: flag(settings.paused), theme: choice(settings.theme, ["light", "dark"]),
      charge: finite(settings.charge), linkDistance: finite(settings.linkDistance),
      showLabels: flag(settings.showLabels), showProperties: flag(settings.showProperties),
      nodeLabelProperty: property(settings.nodeLabelProperty), edgeLabelProperty: property(settings.edgeLabelProperty),
      edgeLabels: choice(settings.edgeLabels, ["off", "selected", "all"]), labelMaxLength,
      nodeSizeProperty: property(settings.nodeSizeProperty), nodeColorProperty: property(settings.nodeColorProperty),
      edgeWidthProperty: property(settings.edgeWidthProperty), edgeColorProperty: property(settings.edgeColorProperty),
    },
    filters: { query: text(filters.query), hiddenGroups: strings(filters.hiddenGroups), hiddenTypes: strings(filters.hiddenTypes),
      requiredLabel: text(filters.requiredLabel), focusNodeId, focusDirection: choice(filters.focusDirection, ["in", "out", "both"]),
      focusHops, searchProperties: flag(filters.searchProperties), highlightOn: flag(filters.highlightOn) },
  };
}
