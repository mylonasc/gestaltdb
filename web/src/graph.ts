// Payload helpers shared by the canvas, legend, and toolbar (VIZ-04).
import type { VizPayload } from "./viz-types";

export function groupKey(node: { group: string; labels: string[] }): string {
  return node.group || node.labels[0] || "(unlabeled)";
}

export function typeKey(edge: { type: string }): string {
  return edge.type || "(untyped)";
}

export function legendGroups(payload: VizPayload): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const node of payload.nodes) {
    const key = groupKey(node);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
}

export function legendTypes(payload: VizPayload): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const edge of payload.edges) {
    const key = typeKey(edge);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
}
