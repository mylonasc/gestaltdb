import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { VizPayload } from "./viz-types";
import { colorFor } from "./colors";
import { groupKey, typeKey } from "./graph";

export interface CanvasSettings {
  paused: boolean;
  charge: number;
  linkDistance: number;
  showLabels: boolean;
}

interface Props {
  payload: VizPayload;
  settings: CanvasSettings;
  /** Increment to refit the viewport to all nodes. */
  fitSignal: number;
  /** Increment to release all pinned nodes. */
  unpinSignal: number;
}

interface SimNode extends d3.SimulationNodeDatum {
  uid: string;
  group: string;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  uid: string;
  etype: string;
}

const NODE_RADIUS = 7;

export function GraphCanvas({ payload, settings, fitSignal, unpinSignal }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const linksRef = useRef<SimLink[]>([]);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  // Build (or rebuild) the simulation whenever the payload identity changes.
  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    const byId = new Map<string, SimNode>();
    payload.nodes.forEach((node, index) => {
      // Deterministic spiral seeding so first paint is sane pre-tick.
      const angle = index * 2.399963;
      const radius = 12 * Math.sqrt(index);
      byId.set(node.id, {
        uid: node.id,
        group: groupKey(node),
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
      links.push({ uid: edge.id, source, target, etype: typeKey(edge) });
    }
    nodesRef.current = nodes;
    linksRef.current = links;

    const defs = svg.append("defs");
    const types = [...new Set(links.map((link) => link.etype))];
    types.forEach((etype, index) => {
      defs
        .append("marker")
        .attr("id", `gdviz-arrow-${index}`)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", NODE_RADIUS + 7)
        .attr("refY", 0)
        .attr("markerWidth", 7)
        .attr("markerHeight", 7)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", colorFor(etype));
    });
    const typeIndex = new Map(types.map((etype, index) => [etype, index] as [string, number]));

    const viewport = svg.append("g").attr("class", "viewport");
    const edgeLayer = viewport.append("g").attr("class", "edges");
    const loopLayer = viewport.append("g").attr("class", "loops");
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
      .force("collide", d3.forceCollide(NODE_RADIUS + 4));
    simRef.current = sim;
    if (settingsRef.current.paused) sim.stop();

    const plainLinks = links.filter((link) => link.source !== link.target);
    const loopLinks = links.filter((link) => link.source === link.target);

    const edgeSel = edgeLayer
      .selectAll<SVGLineElement, SimLink>("line")
      .data(plainLinks, (link) => link.uid)
      .join("line")
      .attr("stroke", (link) => colorFor(link.etype))
      .attr("stroke-width", 1.2)
      .attr("stroke-opacity", 0.7)
      .attr("marker-end", (link) => `url(#gdviz-arrow-${typeIndex.get(link.etype) ?? 0})`);

    const loopSel = loopLayer
      .selectAll<SVGPathElement, SimLink>("path")
      .data(loopLinks, (link) => link.uid)
      .join("path")
      .attr("fill", "none")
      .attr("stroke", (link) => colorFor(link.etype))
      .attr("stroke-width", 1.2)
      .attr("stroke-opacity", 0.7);

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
      })
      .on("end", (event) => {
        if (!event.active) sim.alphaTarget(0);
        // Node stays pinned at its drop point; double-click releases it.
      });

    const nodeSel = nodeLayer
      .selectAll<SVGGElement, SimNode>("g")
      .data(nodes, (node) => node.uid)
      .join("g")
      .attr("class", "node")
      .call(drag)
      .on("dblclick", (_event, node) => {
        node.fx = null;
        node.fy = null;
        if (!settingsRef.current.paused) sim.alpha(0.3).restart();
      });
    nodeSel
      .append("circle")
      .attr("r", NODE_RADIUS)
      .attr("fill", (node) => colorFor(node.group))
      .attr("stroke", "currentColor")
      .attr("stroke-width", 1.2);
    nodeSel.append("title").text((node) => node.uid);

    const labelSel = labelLayer
      .selectAll<SVGTextElement, SimNode>("text")
      .data(nodes, (node) => node.uid)
      .join("text")
      .attr("class", "node-label")
      .attr("dx", NODE_RADIUS + 3)
      .attr("dy", 4)
      .text((node) => node.uid);
    labelSel.attr("display", settingsRef.current.showLabels ? null : "none");

    sim.on("tick", () => {
      edgeSel
        .attr("x1", (link) => (link.source as SimNode).x ?? 0)
        .attr("y1", (link) => (link.source as SimNode).y ?? 0)
        .attr("x2", (link) => (link.target as SimNode).x ?? 0)
        .attr("y2", (link) => (link.target as SimNode).y ?? 0);
      loopSel.attr("d", (link) => {
        const node = link.source as SimNode;
        const x = node.x ?? 0;
        const y = node.y ?? 0;
        const r = NODE_RADIUS + 6;
        return `M ${x} ${y - NODE_RADIUS} C ${x + r * 2} ${y - r * 2}, ${x + r * 2} ${y + r}, ${x} ${y + NODE_RADIUS}`;
      });
      nodeSel.attr("transform", (node) => `translate(${node.x ?? 0},${node.y ?? 0})`);
      labelSel.attr("x", (node) => node.x ?? 0).attr("y", (node) => node.y ?? 0);
    });

    // Initial fit once geometry exists.
    fitViewport();

    return () => {
      sim.stop();
      simRef.current = null;
      svg.selectAll("*").remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  function fitViewport() {
    const svgEl = svgRef.current;
    const zoom = zoomRef.current;
    if (!svgEl || !zoom) return;
    const nodes = nodesRef.current;
    if (nodes.length === 0) return;
    const width = svgEl.clientWidth || 800;
    const height = svgEl.clientHeight || 600;
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const node of nodes) {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
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
        .forceLink<SimNode, SimLink>(linksRef.current)
        .id((node) => node.uid)
        .distance(settings.linkDistance)
    );
    if (!settings.paused) sim.alpha(0.5).restart();
  }, [settings.charge, settings.linkDistance, settings.paused]);

  // Label visibility.
  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    d3.select(svgEl)
      .select("g.labels")
      .selectAll("text")
      .attr("display", settings.showLabels ? null : "none");
  }, [settings.showLabels]);

  // Imperative signals.
  useEffect(() => {
    if (fitSignal > 0) fitViewport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitSignal]);

  useEffect(() => {
    if (unpinSignal === 0) return;
    for (const node of nodesRef.current) {
      node.fx = null;
      node.fy = null;
    }
    const sim = simRef.current;
    if (sim && !settingsRef.current.paused) sim.alpha(0.3).restart();
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
