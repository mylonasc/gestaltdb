/** Attribute mappings with bounded ranges and explicit missing-value fallbacks. */
import { colorFor } from "./colors";
import { groupKey, typeKey } from "./graph";
import type { AppearanceOptions, VizPayload, VizPayloadNode, VizPayloadEdge } from "./viz-types";

type Entity = VizPayloadNode | VizPayloadEdge;
export function propertyValue(entity: Pick<Entity, "properties" | "extra">, property: string | null): unknown {
  if (!property) return undefined;
  const value = Object.prototype.hasOwnProperty.call(entity.properties, property) ? entity.properties[property] : undefined;
  return value ?? (Object.prototype.hasOwnProperty.call(entity.extra, property) ? entity.extra[property] : undefined);
}
function category(value: unknown, fallback: string): string {
  if (value === undefined || value === null) return fallback;
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function numericScale(entities: Entity[], property: string | null, range: [number, number], fallback: number) {
  const values = entities.map(entity => propertyValue(entity, property)).filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 0;
  const magnitude = Math.max(Math.abs(low), Math.abs(high)) || 1;
  return (entity: Entity) => {
    const value = propertyValue(entity, property);
    if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
    const fraction = high === low ? 0.5 : Math.max(0, Math.min(1,
      (value / magnitude - low / magnitude) / (high / magnitude - low / magnitude)));
    return range[0] + fraction * (range[1] - range[0]);
  };
}
export function appearanceScales(payload: VizPayload, options: AppearanceOptions) {
  return {
    radius: numericScale(payload.nodes, options.nodeSizeProperty, [4, 24], 7),
    width: numericScale(payload.edges, options.edgeWidthProperty, [0.5, 6], 1.2),
    nodeColor: (node: VizPayloadNode) => colorFor(category(propertyValue(node, options.nodeColorProperty), groupKey(node))),
    edgeColor: (edge: VizPayloadEdge) => colorFor(category(propertyValue(edge, options.edgeColorProperty), typeKey(edge))),
  };
}

export function propertyNames(entities: Entity[]): string[] {
  return [...new Set(entities.flatMap(entity => [...Object.keys(entity.properties), ...Object.keys(entity.extra)]))].sort();
}
