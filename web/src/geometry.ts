/** Stable multigraph lanes and clipped paths, shared by strokes, hit targets and labels. */
export interface Point { x?: number; y?: number }
export interface Endpoints { id: string; source: string; target: string }

export function edgeLanes(edges: Endpoints[]): Map<string, number> {
  const groups = new Map<string, Endpoints[]>();
  for (const edge of edges) {
    const key = JSON.stringify([edge.source, edge.target].sort());
    const group = groups.get(key) ?? [];
    group.push(edge);
    groups.set(key, group);
  }
  const lanes = new Map<string, number>();
  for (const group of groups.values()) {
    group.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    group.forEach((edge, index) => {
      const lane = edge.source === edge.target ? index + 1 : index - (group.length - 1) / 2;
      lanes.set(edge.id, edge.source > edge.target ? -lane : lane);
    });
  }
  return lanes;
}

export function edgeGeometry(source: Point, target: Point, lane: number, loop: boolean, radius = 7, targetRadius = radius) {
  const x = source.x ?? 0, y = source.y ?? 0;
  const tx = target.x ?? 0, ty = target.y ?? 0;
  if (loop) {
    const r = radius + 12 + lane * 12;
    return { path: `M ${x} ${y - radius} C ${x + r * 2} ${y - r * 2}, ${x + r * 2} ${y + r}, ${x} ${y + radius}`,
      x: x + 1.5 * r, y: y - 0.375 * r };
  }
  const dx = tx - x, dy = ty - y, distance = Math.hypot(dx, dy) || 1;
  const cx = (x + tx) / 2 - dy / distance * lane * 36;
  const cy = (y + ty) / 2 + dx / distance * lane * 36;
  const start = Math.hypot(cx - x, cy - y) || 1;
  const end = Math.hypot(tx - cx, ty - cy) || 1;
  const sx = x + (cx - x) / start * radius, sy = y + (cy - y) / start * radius;
  const ex = tx - (tx - cx) / end * (targetRadius + 3), ey = ty - (ty - cy) / end * (targetRadius + 3);
  return { path: `M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`,
    x: (sx + 2 * cx + ex) / 4, y: (sy + 2 * cy + ey) / 4 };
}
