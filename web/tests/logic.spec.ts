import { test, expect } from "@playwright/test";
import { computeView } from "../src/view";
import { appearanceScales } from "../src/appearance";
import { treeCoordinates } from "../src/GraphCanvas";
import { graphIdentity, parseViewState, type ViewState } from "../src/session";
import { DEFAULT_SETTINGS, type VizPayload } from "../src/viz-types";
import { entityLabel } from "../src/labels";

const payload: VizPayload = {
  version: 1,
  nodes: [
    { id: "a", labels: ["Person"], group: "Person", properties: { name: "Alice", mass: 1, kind: "same" }, extra: {} },
    { id: "b", labels: ["Person", "Partner"], group: "Person", properties: { mass: 9, kind: "same" }, extra: {} },
    { id: "c", labels: ["Other"], group: "Other", properties: { mass: true }, extra: {} },
  ],
  edges: [
    { id: "ab", source: "a", target: "b", type: "T", properties: {}, extra: { score: 1 } },
    { id: "bc", source: "b", target: "c", type: "U", properties: {}, extra: { score: 3 } },
  ],
  highlight: { nodes: [], edges: [] }, truncation: { nodes_dropped: 0, edges_dropped: 0, truncated: false }, meta: {},
};
const filters: ViewState["filters"] = { query: "", hiddenGroups: [], hiddenTypes: [], hiddenShapes: [], requiredLabel: "", focusNodeId: null,
  focusDirection: "both", focusHops: 1, searchProperties: true, highlightOn: false };
function view(overrides: Partial<ViewState["filters"]> = {}) {
  const values = { ...filters, ...overrides };
  const shapes = values.hiddenShapes ?? [];
  return computeView(payload, { ...values, hiddenGroups: new Set(values.hiddenGroups), hiddenTypes: new Set(values.hiddenTypes),
    hiddenShapes: new Set(shapes) });
}
function saved(): ViewState {
  return { version: 1, kind: "gestaltdb-view", graph: graphIdentity(payload), settings: { ...DEFAULT_SETTINGS }, filters: { ...filters }, selection: null,
    layout: { zoom: { x: 10, y: 20, k: 1 }, simulation: { nodes: ["a", "b", "c"], edges: ["ab", "bc"] },
      nodes: payload.nodes.map((node, index) => ({ id: node.id, x: index, y: 0, pinned: false })) } };
}

test("bounded directed focus respects relationship and native-label filters", () => {
  expect(view({ focusNodeId: "a", focusDirection: "out", focusHops: 1 }).hiddenNodes).toEqual(["c"]);
  expect(view({ focusNodeId: "a", focusDirection: "out", focusHops: 2 }).hiddenNodes).toEqual([]);
  expect(view({ focusNodeId: "a", focusDirection: "in", focusHops: 2 }).hiddenNodes).toEqual(["b", "c"]);
  expect(view({ focusNodeId: "a", focusHops: 2, hiddenTypes: ["U"] }).hiddenNodes).toEqual(["c"]);
  expect(view({ requiredLabel: "Partner" }).hiddenNodes).toEqual(["a", "c"]);
  expect(view({ focusNodeId: "a", focusHops: 2, hiddenGroups: ["Person"] }).visibleNodeCount).toBe(0);
  expect(view({ query: "alice" }).hiddenNodes).toEqual(["b", "c"]);
  expect(view({ query: "alice", searchProperties: false }).visibleNodeCount).toBe(0);
});

test("attribute scales exclude booleans and missing values and retain global ranges", () => {
  const scales = appearanceScales(payload, { ...DEFAULT_SETTINGS, nodeSizeProperty: "mass", edgeWidthProperty: "score", nodeColorProperty: "kind" });
  expect(payload.nodes.map(scales.radius)).toEqual([8, 32, 12]);
  expect(payload.edges.map(scales.width)).toEqual([1, 7]);
  expect(scales.nodeColor(payload.nodes[0])).toBe(scales.nodeColor(payload.nodes[1]));
  const extreme = { ...payload, nodes: payload.nodes.map((node, index) => ({ ...node, properties: { mass: index ? 1e308 : -1e308 } })) };
  const bounded = appearanceScales(extreme, { ...DEFAULT_SETTINGS, nodeSizeProperty: "mass" });
  expect(extreme.nodes.map(bounded.radius)).toEqual([8, 32, 32]);
  expect(entityLabel(payload.nodes[0], "constructor", "fallback", 40)).toBe("fallback");
});

test("layered tree coordinates are deterministic and top-down", () => {
  const nodes = [{ uid: "a" }, { uid: "b" }, { uid: "c" }];
  const links = [{ source: "a", target: "b" }, { source: "b", target: "c" }];
  const positions = treeCoordinates(nodes, links);
  expect(positions.get("a")!.y).toBeLessThan(positions.get("b")!.y);
  expect(positions.get("b")!.y).toBeLessThan(positions.get("c")!.y);
  expect(positions.get("a")!.x).toBe(0);
  expect([...positions.entries()]).toEqual([...treeCoordinates(nodes, links).entries()]);
  // Cyclic leftovers take trailing rows in uid order instead of collapsing.
  const cyclic = treeCoordinates([{ uid: "a" }, { uid: "b" }],
    [{ source: "a", target: "b" }, { source: "b", target: "a" }]);
  expect(cyclic.get("a")!.y).toBeLessThan(cyclic.get("b")!.y);
});

test("shape visibility hides whole node layers and survives saved views", () => {
  expect(view({ hiddenShapes: ["circle"] }).hiddenNodes).toEqual(["a", "b", "c"]);
  expect(view({ hiddenShapes: ["triangle"] }).hiddenNodes).toEqual([]);
  const state = saved();
  expect(state.filters.hiddenShapes).toEqual([]);
  expect(parseViewState(JSON.parse(JSON.stringify(state)), payload).filters.hiddenShapes).toEqual([]);
  const legacy = JSON.parse(JSON.stringify(state));
  delete legacy.filters.hiddenShapes;
  expect(parseViewState(legacy, payload).filters.hiddenShapes).toEqual([]);
});

test("view state validates topology, complete coordinates, settings and identity before restoration", () => {
  const state = saved();
  expect(parseViewState(JSON.parse(JSON.stringify(state)), payload)).toEqual(state);
  const mismatched = saved(); mismatched.graph.edges[0][2] = "c";
  expect(() => parseViewState(mismatched, payload)).toThrow("different graph topology");
  const duplicate = saved(); duplicate.layout.nodes[1] = duplicate.layout.nodes[0];
  expect(() => parseViewState(duplicate, payload)).toThrow("duplicate node");
  const missing = saved(); missing.layout.nodes.pop();
  expect(() => parseViewState(missing, payload)).toThrow("missing node");
  const invalid = saved(); invalid.layout.nodes[0].x = Infinity;
  expect(() => parseViewState(invalid, payload)).toThrow("finite numbers");
  const zoom = saved(); zoom.layout.zoom.k = 0;
  expect(() => parseViewState(zoom, payload)).toThrow("zoom");
  const settings = saved(); (settings.settings as unknown as Record<string, unknown>).showLabels = "yes";
  expect(() => parseViewState(settings, payload)).toThrow("boolean");
});

