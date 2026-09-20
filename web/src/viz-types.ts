// Payload schema mirror of src/gestaltdb/viz/ir.py (VIZ-01).
// Keep in sync: version, nodes, edges, highlight, truncation, meta.

export interface VizPayloadNode {
  id: string;
  labels: string[];
  group: string;
  properties: Record<string, unknown>;
  extra: Record<string, unknown>;
}

export interface VizPayloadEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: Record<string, unknown>;
  extra: Record<string, unknown>;
}

export interface VizPayload {
  version: 1;
  nodes: VizPayloadNode[];
  edges: VizPayloadEdge[];
  highlight: { nodes: string[]; edges: string[] };
  truncation: { nodes_dropped: number; edges_dropped: number; truncated: boolean };
  meta: Record<string, unknown>;
}

declare global {
  interface Window {
    __GESTALTDB_VIZ__?: VizPayload;
    __GESTALTDB_VIZ_OPTIONS__?: VizViewOptions;
  }
}

export interface VizViewOptions {
  theme?: "light" | "dark";
  charge?: number;
  linkDistance?: number;
  showLabels?: boolean;
  showProperties?: boolean;
}
