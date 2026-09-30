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

export interface AppearanceOptions {
  nodeSizeProperty: string | null;
  nodeColorProperty: string | null;
  edgeWidthProperty: string | null;
  edgeColorProperty: string | null;
}

export interface ViewerSettings extends AppearanceOptions {
  paused: boolean;
  theme: "light" | "dark";
  charge: number;
  linkDistance: number;
  showLabels: boolean;
  showProperties: boolean;
  nodeLabelProperty: string | null;
  edgeLabelProperty: string | null;
  edgeLabels: "off" | "selected" | "all";
  labelMaxLength: number;
}

export type VizViewOptions = Partial<ViewerSettings>;

export const DEFAULT_SETTINGS: ViewerSettings = {
  paused: false, theme: "light", charge: -300, linkDistance: 60, showLabels: true, showProperties: true,
  nodeLabelProperty: null, edgeLabelProperty: null, edgeLabels: "selected", labelMaxLength: 40,
  nodeSizeProperty: null, nodeColorProperty: null, edgeWidthProperty: null, edgeColorProperty: null,
};
