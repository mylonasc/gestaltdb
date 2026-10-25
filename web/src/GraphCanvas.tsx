import { useEffect, useId, useImperativeHandle, useMemo, useRef, type Ref, type RefObject } from "react";
import * as d3 from "d3";
import type { VizPayload, ViewerSettings } from "./viz-types";
import type { Selection } from "./Inspector";
import { PALETTE } from "./colors";
import { groupKey, typeKey } from "./graph";
import { edgeGeometry, edgeLanes } from "./geometry";
import { entityLabel } from "./labels";
import { appearanceScales } from "./appearance";
import type { LayoutState } from "./session";

export interface CanvasHandle {
  snapshot: () => LayoutState;
  restore: (state: LayoutState) => void;
  togglePin: (id: string) => void;
  relayout: (visibleOnly: boolean) => void;
  /** Pin every node at deterministic layered (tree) coordinates. */
  treeLayout: () => void;
}

interface Props {
  canvasRef: RefObject<SVGSVGElement>;
  controllerRef: Ref<CanvasHandle>;
  onPinsChange: (ids: string[]) => void;
  payload: VizPayload;
  settings: ViewerSettings;
  /** Increment to refit the viewport to visible geometry. */
  fitSignal: number;
  /** Increment to release all pinned nodes. */
  unpinSignal: number;
  selection: Selection | null;
  onSelect: (selection: Selection | null) => void;
  hiddenNodes: string[];
  hiddenEdges: string[];
  dimNodes: string[];
  dimEdges: string[];
}

interface SimNode extends d3.SimulationNodeDatum {
  uid: string;
  group: string;
  radius: number;
  shape: "circle" | "triangle" | "square";
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  uid: string;
  etype: string;
  dash: string | null;
}

/** Deterministic layered coordinates (top-down tree layout).
 *
 * Kahn layering over the given links in sorted uid order: nodes with no
 * incoming link form the top row and every other node sits below its
 * predecessors, so proof trees, derivation graphs, and hierarchies read
 * top-down. Nodes on purely cyclic paths take trailing rows in uid order
 * instead of collapsing onto one row. Mirrors the Python SVG "tree" layout
 * so static exports and the viewer agree byte-for-byte on layering.
 */
export function treeCoordinates<Node extends { uid: string }, Link extends { source: string | number | Node; target: string | number | Node }>(
  nodes: Node[], links: Link[],
): Map<string, { x: number; y: number }> {
  const ids = nodes.map(node => node.uid);
  const members = new Set(ids);
  const children = new Map<string, Set<string>>(ids.map(id => [id, new Set()]));
  const indegree = new Map<string, number>(ids.map(id => [id, 0]));
  const endpoint = (value: string | number | Node): string => typeof value === "object" ? value.uid : String(value);
  for (const link of links) {
    const source = endpoint(link.source), target = endpoint(link.target);
    if (source === target || !members.has(source) || !members.has(target)) continue;
    if (!children.get(source)!.has(target)) {
      children.get(source)!.add(target);
      indegree.set(target, (indegree.get(target) ?? 0) + 1);
    }
  }
  const depth = new Map<string, number>();
  let current = ids.filter(id => (indegree.get(id) ?? 0) === 0).sort();
  let layer = 0;
  while (current.length > 0) {
    const upcoming: string[] = [];
    for (const id of current) {
      depth.set(id, layer);
      for (const child of [...children.get(id)!].sort()) {
        indegree.set(child, (indegree.get(child) ?? 0) - 1);
        if (indegree.get(child) === 0) upcoming.push(child);
      }
    }
    current = [...new Set(upcoming)].sort();
    layer += 1;
  }
  for (const id of [...members].sort()) {
    if (!depth.has(id)) { depth.set(id, layer); layer += 1; }
  }
  const rows = new Map<number, string[]>();
  for (const id of ids) {
    const row = depth.get(id)!;
    if (!rows.has(row)) rows.set(row, []);
    rows.get(row)!.push(id);
  }
  const positions = new Map<string, { x: number; y: number }>();
  for (const row of [...rows.keys()].sort((a, b) => a - b)) {
    const rowMembers = rows.get(row)!.sort();
    rowMembers.forEach((id, index) => {
      positions.set(id, { x: (index - (rowMembers.length - 1) / 2) * 200, y: row * 160 });
    });
  }
  return positions;
}

export function GraphCanvas({
  canvasRef,
  controllerRef,
  onPinsChange,
  payload,
  settings,
  fitSignal,
  unpinSignal,
  selection,
  onSelect,
  hiddenNodes,
  hiddenEdges,
  dimNodes,
  dimEdges,
}: Props) {
  const svgRef = canvasRef;
  const nodeEntities = useMemo(() => new Map(payload.nodes.map((node) => [node.id, node])), [payload]);
  const edgeEntities = useMemo(() => new Map(payload.edges.map((edge) => [edge.id, edge])), [payload]);
  const instanceId = useId().replace(/:/g, "");
  const scales = useMemo(() => appearanceScales(payload, settings), [payload,
    settings.nodeSizeProperty, settings.nodeColorProperty, settings.edgeWidthProperty, settings.edgeColorProperty]);
  const scalesRef = useRef(scales);
  scalesRef.current = scales;
  const renderRef = useRef<() => void>(() => {});
  const pinsChangeRef = useRef(onPinsChange);
  pinsChangeRef.current = onPinsChange;
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const linksRef = useRef<SimLink[]>([]);
  const activeLinksRef = useRef<SimLink[]>([]);
  const nodeSelRef = useRef<d3.Selection<SVGGElement, SimNode, SVGGElement, unknown> | null>(null);
  const labelSelRef = useRef<d3.Selection<SVGTextElement, SimNode, SVGGElement, unknown> | null>(null);
  const edgeSelRef = useRef<d3.Selection<SVGPathElement, SimLink, SVGGElement, unknown> | null>(null);
  const hitSelRef = useRef<d3.Selection<SVGPathElement, SimLink, SVGGElement, unknown> | null>(null);
  const edgeLabelRef = useRef<d3.Selection<SVGTextElement, SimLink, SVGGElement, unknown> | null>(null);
  const hoveredEdge = useRef<string | null>(null);
  const settingsRef = useRef(settings);
  const restoredViewportRef = useRef(false);
  settingsRef.current = settings;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const viewRef = useRef({ selection, hiddenNodes, hiddenEdges, dimNodes, dimEdges, showLabels: settings.showLabels });
  viewRef.current = { selection, hiddenNodes, hiddenEdges, dimNodes, dimEdges, showLabels: settings.showLabels };

  function notifyPins() {
    pinsChangeRef.current(nodesRef.current.filter(node => node.fx != null).map(node => node.uid));
  }

  // Reheat a stopped simulation gently: releasing a pin far from force
  // equilibrium (e.g. one tree node among pinned anchors) must ease it
  // there, never catapult it across the canvas. Hotter running states keep
  // their energy. Arrange paths always cool the sim to zero first, so a
  // stopped diagram never resumes at a stale hot alpha.
  function wakeSim() {
    const sim = simRef.current;
    if (sim && !settingsRef.current.paused) {
      sim.alpha(Math.max(sim.alpha(), 0.08)).restart();
    }
  }

  // Deterministic layered arrangement: pin every node at its tree
  // coordinates and stop the force simulation there. Unpinning (or Reset
  // layout) hands control back to the force-directed layout.
  function arrangeTree() {
    const positions = treeCoordinates(nodesRef.current, linksRef.current);
    // Center the rows on the simulation origin so the center force stays
    // balanced; otherwise the first released node gets yanked toward it.
    let top = Infinity, bottom = -Infinity;
    for (const point of positions.values()) {
      if (point.y < top) top = point.y;
      if (point.y > bottom) bottom = point.y;
    }
    const shift = (top + bottom) / 2;
    const sim = simRef.current;
    // Cool to zero: a stopped diagram must never resume at a stale hot alpha.
    sim?.alpha(0);
    sim?.stop();
    for (const node of nodesRef.current) {
      const point = positions.get(node.uid)!;
      node.x = point.x; node.y = point.y - shift; node.vx = 0; node.vy = 0;
      node.fx = node.x; node.fy = node.y;
    }
    renderRef.current(); notifyPins(); fitViewport();
  }

  useImperativeHandle(controllerRef, () => ({
    snapshot: () => {
      const zoom = svgRef.current ? d3.zoomTransform(svgRef.current) : d3.zoomIdentity;
      return { nodes: nodesRef.current.map(node => ({ id: node.uid, x: node.x ?? 0, y: node.y ?? 0, pinned: node.fx != null })),
        zoom: { x: zoom.x, y: zoom.y, k: zoom.k },
        simulation: { nodes: (simRef.current?.nodes() ?? []).map(node => node.uid), edges: activeLinksRef.current.map(link => link.uid) } };
    },
    restore: state => {
      // Imported pan/zoom belongs to the user. Status/inspector changes can
      // resize the canvas after this effect and must not silently auto-fit it.
      restoredViewportRef.current = true;
      simRef.current?.stop();
      const activeNodes = new Set(state.simulation.nodes), activeEdges = new Set(state.simulation.edges);
      activeLinksRef.current = linksRef.current.filter(link => activeEdges.has(link.uid));
      simRef.current?.force("link", null).nodes(nodesRef.current.filter(node => activeNodes.has(node.uid))).force("link",
        d3.forceLink<SimNode, SimLink>(activeLinksRef.current).id(node => node.uid).distance(settingsRef.current.linkDistance));
      const positions = new Map(state.nodes.map(node => [node.id, node]));
      for (const node of nodesRef.current) {
        const saved = positions.get(node.uid)!;
        node.x = saved.x; node.y = saved.y; node.vx = 0; node.vy = 0;
        node.fx = saved.pinned ? saved.x : null; node.fy = saved.pinned ? saved.y : null;
      }
      renderRef.current();
      notifyPins();
      if (svgRef.current && zoomRef.current) {
        d3.select(svgRef.current).interrupt().call(zoomRef.current.transform,
          d3.zoomIdentity.translate(state.zoom.x, state.zoom.y).scale(state.zoom.k));
      }
      wakeSim();
    },
    togglePin: id => {
      const node = nodesRef.current.find(item => item.uid === id);
      if (!node) return;
      const pinned = node.fx != null;
      node.fx = pinned ? null : node.x; node.fy = pinned ? null : node.y;
      renderRef.current(); notifyPins();
      wakeSim();
    },
    relayout: visibleOnly => {
      const hidden = new Set(viewRef.current.hiddenNodes);
      const hiddenEdges = new Set(viewRef.current.hiddenEdges);
      const nodes = nodesRef.current.filter(node => !visibleOnly || !hidden.has(node.uid));
      const links = linksRef.current.filter(link => !visibleOnly || !hiddenEdges.has(link.uid));
      activeLinksRef.current = links;
      nodes.forEach((node, index) => {
        const angle = index * 2.399963, radius = 12 * Math.sqrt(index);
        node.x = Math.cos(angle) * radius; node.y = Math.sin(angle) * radius;
        node.fx = null; node.fy = null; node.vx = 0; node.vy = 0;
      });
      const sim = simRef.current;
      sim?.stop();
      // Clear old links before replacing nodes: D3 initializes forces on nodes().
      sim?.force("link", null).nodes(nodes).force("link", d3.forceLink<SimNode, SimLink>(links)
        .id(node => node.uid).distance(settingsRef.current.linkDistance));
      sim?.alpha(1);
      if (settingsRef.current.paused) sim?.tick(100);
      else sim?.restart();
      renderRef.current(); notifyPins(); fitViewport();
    },
    treeLayout: () => {
      arrangeTree();
    },
  }), []);

  // Build (or rebuild) the simulation whenever the payload identity changes.
  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();
    svg.on("click", () => onSelectRef.current(null));

    const byId = new Map<string, SimNode>();
    payload.nodes.forEach((node, index) => {
      // Deterministic spiral seeding so first paint is sane pre-tick.
      const angle = index * 2.399963;
      const radius = 12 * Math.sqrt(index);
      byId.set(node.id, {
        uid: node.id,
        group: groupKey(node),
        radius: scalesRef.current.radius(node),
        shape: node.shape ?? "circle",
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      });
    });
    const nodes = [...byId.values()];
    const links: SimLink[] = [];
    for (const edge of payload.edges) {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      if (!source || !target) continue;
      links.push({ uid: edge.id, source, target, etype: typeKey(edge), dash: edge.dash ?? null });
    }
    nodesRef.current = nodes;
    linksRef.current = links;
    activeLinksRef.current = links;

    const defs = svg.append("defs");
    PALETTE.forEach((color, index) => {
      defs
        .append("marker")
        .attr("id", `gdviz-arrow-${instanceId}-${index}`)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 10)
        .attr("refY", 0)
        .attr("markerWidth", 7)
        .attr("markerHeight", 7)
        .attr("markerUnits", "userSpaceOnUse")
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", color);
    });

    const viewport = svg.append("g").attr("class", "viewport");
    const edgeLayer = viewport.append("g").attr("class", "edges");
    const hitLayer = viewport.append("g").attr("class", "hits");
    const nodeLayer = viewport.append("g").attr("class", "nodes");
    const labelLayer = viewport.append("g").attr("class", "labels");

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 8])
      .on("zoom", (event) => viewport.attr("transform", event.transform));
    svg.call(zoom);
    zoomRef.current = zoom;

    const sim = d3
      .forceSimulation<SimNode, SimLink>(nodes)
      .force(
        "link",
        d3
          .forceLink<SimNode, SimLink>(links)
          .id((node) => node.uid)
          .distance(settingsRef.current.linkDistance)
      )
      .force("charge", d3.forceManyBody().strength(settingsRef.current.charge))
      .force("center", d3.forceCenter(0, 0))
      .force("collide", d3.forceCollide<SimNode>(node => node.radius + 4));
    simRef.current = sim;
    if (settingsRef.current.paused) sim.stop();

    const lanes = edgeLanes(payload.edges);
    const geometry = (link: SimLink) => edgeGeometry(link.source as SimNode, link.target as SimNode,
      lanes.get(link.uid) ?? 0, link.source === link.target, (link.source as SimNode).radius, (link.target as SimNode).radius);

    const edgeSel = edgeLayer
      .selectAll<SVGPathElement, SimLink>("path")
      .data(links, (link) => link.uid)
      .join("path")
      .attr("class", "edge")
      .attr("fill", "none")
      .attr("stroke-width", 1.2)
      .attr("stroke-dasharray", (link) => link.dash)
      .attr("stroke-opacity", 0.7);
    edgeSelRef.current = edgeSel;

    // Wide invisible hit targets make thin edges clickable/keyboardable.
    const hitSel = hitLayer
      .selectAll<SVGPathElement, SimLink>("path")
      .data(links, (link) => link.uid)
      .join("path")
      .attr("class", "edge-hit")
      .attr("fill", "none")
      .attr("stroke", "transparent")
      .attr("stroke-width", 10)
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (link) => `Edge ${link.uid} (${link.etype})`)
      .on("mouseenter focus", (_event, link) => { hoveredEdge.current = link.uid; applyView(); })
      .on("mouseleave blur", () => { hoveredEdge.current = null; applyView(); })
      .on("click", (event, link) => {
        event.stopPropagation();
        onSelectRef.current({ kind: "edge", id: link.uid });
      })
      .on("keydown", (event, link) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelectRef.current({ kind: "edge", id: link.uid });
        }
      });
    hitSel.append("title").text((link) => `${link.uid} (${link.etype})`);
    hitSelRef.current = hitSel;

    const edgeLabelSel = labelLayer.selectAll<SVGTextElement, SimLink>(".edge-label")
      .data(links, (link) => link.uid).join("text").attr("class", "edge-label")
      .attr("text-anchor", "middle").attr("dy", -4);
    edgeLabelRef.current = edgeLabelSel;

    const drag = d3
      .drag<SVGGElement, SimNode>()
      .on("start", (event, node) => {
        if (!event.active && !settingsRef.current.paused) sim.alphaTarget(0.3).restart();
        node.fx = node.x;
        node.fy = node.y;
      })
      .on("drag", (event, node) => {
        node.fx = event.x;
        node.fy = event.y;
        if (settingsRef.current.paused) {
          node.x = event.x;
          node.y = event.y;
          renderPositions();
        }
      })
      .on("end", (event) => {
        if (!event.active) sim.alphaTarget(0);
        renderPositions(); notifyPins();
        // Node stays pinned at its drop point; double-click releases it.
      });

    const nodeSel = nodeLayer
      .selectAll<SVGGElement, SimNode>("g")
      .data(nodes, (node) => node.uid)
      .join("g")
      .attr("class", "node")
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (node) => `Node ${node.uid}`)
      .call(drag)
      .on("click", (event, node) => {
        event.stopPropagation();
        onSelectRef.current({ kind: "node", id: node.uid });
      })
      .on("keydown", (event, node) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelectRef.current({ kind: "node", id: node.uid });
        }
      })
      .on("dblclick", (event, node) => {
        event.stopPropagation();
        node.fx = null;
        node.fy = null;
        wakeSim();
        renderPositions(); notifyPins();
      });
    nodeSel
      .each(function (node) {
        const shape = node.shape === "triangle" ? "polygon" : node.shape === "square" ? "rect" : "circle";
        d3.select(this).append(shape).attr("class", "node-shape");
      })
      .append("title")
      .text((node) => node.uid);
    nodeSelRef.current = nodeSel;

    const labelSel = labelLayer
      .selectAll<SVGTextElement, SimNode>(".node-label")
      .data(nodes, (node) => node.uid)
      .join("text")
      .attr("class", "node-label")
      .attr("dx", (node) => node.radius + 3)
      .attr("dy", 4)
      .text((node) => node.uid);
    labelSel.attr("display", settingsRef.current.showLabels ? null : "none");
    labelSelRef.current = labelSel;

    function renderPositions() {
      edgeSel.attr("d", (link) => geometry(link).path);
      hitSel.attr("d", (link) => geometry(link).path);
      edgeLabelSel.attr("x", (link) => geometry(link).x).attr("y", (link) => geometry(link).y);
      nodeSel.attr("transform", (node) => `translate(${node.x ?? 0},${node.y ?? 0})`);
      nodeSel.classed("pinned", node => node.fx != null);
      labelSel.attr("x", (node) => node.x ?? 0).attr("y", (node) => node.y ?? 0)
        .attr("dx", node => node.radius + 3);
    }
    renderRef.current = renderPositions;
    sim.on("tick", renderPositions);
    renderPositions();

    applyView();
    // Initial fit once geometry exists.
    fitViewport();
    // Reasoning artifacts opt into a deterministic tree arrangement via
    // payload meta: pin the layered positions so the first paint is already
    // the final layout instead of a force-simulation lottery.
    if (payload.meta["suggested_layout"] === "tree") arrangeTree();

    return () => {
      sim.stop();
      simRef.current = null;
      nodeSelRef.current = null;
      labelSelRef.current = null;
      edgeSelRef.current = null;
      hitSelRef.current = null;
      edgeLabelRef.current = null;
      renderRef.current = () => {};
      svg.selectAll("*").remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  // Paint every node marker from its shape: circles for data, triangles for
  // inferred conclusions, squares for schema. Geometry refreshes here so
  // attribute-driven radius changes reshape triangles and squares too.
  function paintNodeShapes() {
    nodeSelRef.current
      ?.selectAll<SVGElement, SimNode>(".node-shape")
      .each(function (node) {
        const element = d3.select(this);
        element
          .attr("fill", scalesRef.current.nodeColor(nodeEntities.get(node.uid)!))
          .attr("stroke", "currentColor")
          .attr("stroke-width", 1.5);
        if (node.shape === "triangle") {
          const radius = node.radius;
          element.attr("points", `0,${-radius} ${radius * 0.95},${radius * 0.75} ${-radius * 0.95},${radius * 0.75}`);
        } else if (node.shape === "square") {
          const half = node.radius * 0.9;
          element.attr("x", -half).attr("y", -half).attr("width", half * 2).attr("height", half * 2);
        } else {
          element.attr("r", node.radius);
        }
      });
  }

  function applyView() {
    const view = viewRef.current;
    const hiddenNodeSet = new Set(view.hiddenNodes);
    const hiddenEdgeSet = new Set(view.hiddenEdges);
    const dimNodeSet = new Set(view.dimNodes);
    const dimEdgeSet = new Set(view.dimEdges);
    const selected = view.selection;
    for (const node of nodesRef.current) node.radius = scalesRef.current.radius(nodeEntities.get(node.uid)!);
    paintNodeShapes();
    nodeSelRef.current
      ?.attr("display", (node) => (hiddenNodeSet.has(node.uid) ? "none" : null))
      .attr("opacity", (node) => selected?.kind === "node" && selected.id === node.uid ? 1 : (dimNodeSet.has(node.uid) ? 0.15 : 1))
      .classed("selected", (node) => selected?.kind === "node" && selected.id === node.uid);
    labelSelRef.current
      ?.text((node) => {
        const entity = nodeEntities.get(node.uid)!;
        return entityLabel(entity, settingsRef.current.nodeLabelProperty, node.uid, settingsRef.current.labelMaxLength);
      })
      .attr("display", (node) =>
        hiddenNodeSet.has(node.uid) || !view.showLabels ? "none" : null
      )
      .attr("opacity", (node) => (dimNodeSet.has(node.uid) ? 0.15 : 1));
    edgeSelRef.current
      ?.attr("display", (link) => (hiddenEdgeSet.has(link.uid) ? "none" : null))
      .attr("stroke-opacity", (link) => selected?.kind === "edge" && selected.id === link.uid ? 1 : (dimEdgeSet.has(link.uid) ? 0.12 : 0.7))
      .attr("stroke", link => scalesRef.current.edgeColor(edgeEntities.get(link.uid)!))
      .attr("marker-end", link => `url(#gdviz-arrow-${instanceId}-${PALETTE.indexOf(scalesRef.current.edgeColor(edgeEntities.get(link.uid)!))})`)
      .attr("stroke-width", link => scalesRef.current.width(edgeEntities.get(link.uid)!) +
        ((selected?.kind === "edge" && selected.id === link.uid || hoveredEdge.current === link.uid) ? 2 : 0));
    hitSelRef.current?.attr("display", (link) => (hiddenEdgeSet.has(link.uid) ? "none" : null));
    edgeLabelRef.current
      ?.text((link) => {
        const entity = edgeEntities.get(link.uid)!;
        return entityLabel(entity, settingsRef.current.edgeLabelProperty, entity.type || entity.id, settingsRef.current.labelMaxLength);
      })
      .attr("display", (link) => hiddenEdgeSet.has(link.uid) || settingsRef.current.edgeLabels === "off" ||
        (settingsRef.current.edgeLabels === "selected" && !(selected?.kind === "edge" && selected.id === link.uid) && hoveredEdge.current !== link.uid) ? "none" : null);
    renderRef.current();
  }

  function fitViewport() {
    restoredViewportRef.current = false;
    const svgEl = svgRef.current;
    const zoom = zoomRef.current;
    if (!svgEl || !zoom) return;
    const hidden = new Set(viewRef.current.hiddenNodes);
    const nodes = nodesRef.current.filter((node) => !hidden.has(node.uid));
    if (nodes.length === 0) return;
    const width = svgEl.clientWidth || 800;
    const height = svgEl.clientHeight || 600;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    const viewport = svgEl.querySelector(".viewport") as SVGGElement | null;
    const bounds = viewport?.getBBox();
    for (const node of nodes) {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (bounds && bounds.width > 0 && bounds.height > 0) {
      minX = bounds.x; maxX = bounds.x + bounds.width;
      minY = bounds.y; maxY = bounds.y + bounds.height;
    }
    const spanX = Math.max(maxX - minX, 50);
    const spanY = Math.max(maxY - minY, 50);
    const scale = Math.min(width / (spanX + 120), height / (spanY + 120), 2);
    const tx = width / 2 - scale * ((minX + maxX) / 2);
    const ty = height / 2 - scale * ((minY + maxY) / 2);
    d3.select(svgEl)
      .transition()
      .duration(250)
      .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }

  // Apply view sets (filters, focus, highlight, selection, labels) without
  // restarting the layout.
  useEffect(() => {
    applyView();
  }, [selection, hiddenNodes, hiddenEdges, dimNodes, dimEdges, settings.showLabels,
    settings.edgeLabels, settings.nodeLabelProperty, settings.edgeLabelProperty, settings.labelMaxLength, scales]);

  useEffect(() => {
    simRef.current?.force("collide", d3.forceCollide<SimNode>(node => node.radius + 4));
    if (!settingsRef.current.paused) simRef.current?.alpha(0.3).restart();
  }, [settings.nodeSizeProperty]);

  // Pause / resume.
  useEffect(() => {
    const sim = simRef.current;
    if (!sim) return;
    if (settings.paused) {
      sim.stop();
    } else {
      sim.alpha(0.3).restart();
    }
  }, [settings.paused]);

  // Force parameters.
  useEffect(() => {
    const sim = simRef.current;
    if (!sim) return;
    sim.force("charge", d3.forceManyBody().strength(settings.charge));
    sim.force(
      "link",
        d3
          .forceLink<SimNode, SimLink>(activeLinksRef.current)
        .id((node) => node.uid)
        .distance(settings.linkDistance)
    );
    if (!settings.paused) sim.alpha(0.5).restart();
  }, [settings.charge, settings.linkDistance, settings.paused]);

  // Imperative signals.
  useEffect(() => {
    if (fitSignal > 0) fitViewport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitSignal]);

  // Keep the graph centered when notebook cell dimensions change.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    let initial = true;
    const observer = new ResizeObserver(() => {
      if (initial) { initial = false; return; }
      if (!restoredViewportRef.current) fitViewport();
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (unpinSignal === 0) return;
    for (const node of nodesRef.current) {
      node.fx = null;
      node.fy = null;
    }
    renderRef.current(); notifyPins();
    wakeSim();
  }, [unpinSignal]);

  return (
    <svg
      ref={svgRef}
      className="gdviz-canvas"
      role="img"
      aria-label={`Graph with ${payload.nodes.length} nodes and ${payload.edges.length} edges. Drag nodes to pin them; double-click to release.`}
    />
  );
}
