"""Dependency-free, deterministic SVG rendering of a visualization payload.

Static layouts deliberately do not run the browser force solver. Explicit
coordinates can be supplied to reuse a manually arranged layout.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Mapping
from xml.etree.ElementTree import Element, SubElement, tostring

PALETTE = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#999999")


def _color(key: str) -> str:
    # Match JavaScript FNV-1a over UTF-16 code units, including non-BMP IDs.
    data = key.encode("utf-16-le", errors="surrogatepass")
    value = 2166136261
    for index in range(0, len(data), 2):
        value = ((value ^ int.from_bytes(data[index:index + 2], "little")) * 16777619) & 0xFFFFFFFF
    return PALETTE[value % len(PALETTE)]


def _label(entity: Any, property_name: str | None, fallback: str, limit: int) -> str:
    value = entity.properties.get(property_name, entity.extra.get(property_name)) if property_name else None
    text = fallback if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _property(entity: Any, name: str | None) -> Any:
    return entity.properties.get(name, entity.extra.get(name)) if name else None


def _category(entity: Any, name: str | None, fallback: str) -> str:
    value = _property(entity, name)
    if value is None:
        return fallback
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _numeric_sizes(entities: Any, name: str | None, low: float, high: float, fallback: float) -> dict[str, float]:
    values = {entity.id: _property(entity, name) for entity in entities}
    numeric: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except OverflowError:
            continue
        if math.isfinite(number):
            numeric[key] = number
    if not numeric:
        return {entity.id: fallback for entity in entities}
    minimum, maximum = min(numeric.values()), max(numeric.values())
    magnitude = max(abs(minimum), abs(maximum)) or 1
    return {entity.id: fallback if entity.id not in numeric else
            low + (0.5 if maximum == minimum else
                   (numeric[entity.id] / magnitude - minimum / magnitude) /
                   (maximum / magnitude - minimum / magnitude)) * (high - low)
            for entity in entities}


def render_svg(viz: Any, options: Any, *, width: int = 1200, height: int = 800,
               layout: str = "circle", positions: Mapping[str, tuple[float, float]] | None = None,
               background: bool = True) -> str:
    """Render a circle/grid layout or explicit coordinates fitted to the image.

    Coordinates must cover every node and are normalized uniformly into the
    output viewport. Static edge labels appear only for ``edge_labels='all'``:
    static figures have no selection or hover state.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height)):
        raise ValueError("width and height must be positive integers")
    if layout not in ("circle", "grid"):
        raise ValueError("layout must be 'circle' or 'grid'")
    ids = [node.id for node in viz.nodes]
    count = len(ids)
    coords: dict[str, tuple[float, float]] = {}
    for index, node_id in enumerate(ids):
        if positions is not None:
            if node_id not in positions:
                raise ValueError(f"positions missing node {node_id!r}")
            x, y = positions[node_id]
            x, y = float(x), float(y)
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("positions must contain finite coordinates")
        elif layout == "grid":
            columns = math.ceil(math.sqrt(count))
            x, y = float(index % columns), float(index // columns)
        else:
            angle = 2 * math.pi * index / max(count, 1) - math.pi / 2
            x, y = math.cos(angle), math.sin(angle)
        coords[node_id] = x, y
    if coords:
        magnitude = max(max(abs(x), abs(y)) for x, y in coords.values()) or 1
        coords = {key: (x / magnitude, y / magnitude) for key, (x, y) in coords.items()}
        xs, ys = zip(*coords.values())
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        padding = min(100, width / 4, height / 4)
        scale = min((width - 2 * padding) / max(max_x - min_x, 1e-12),
                    (height - 2 * padding) / max(max_y - min_y, 1e-12))
        coords = {key: (width / 2 + (x - (min_x + max_x) / 2) * scale,
                        height / 2 + (y - (min_y + max_y) / 2) * scale) for key, (x, y) in coords.items()}

    fg, bg = ("#e8e8e6", "#14161a") if options.theme == "dark" else ("#1a1a1a", "#ffffff")
    root = Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": str(width), "height": str(height),
                           "viewBox": f"0 0 {width} {height}", "role": "img", "aria-label": options.title})
    SubElement(root, "title").text = options.title
    if background:
        SubElement(root, "rect", {"width": "100%", "height": "100%", "fill": bg})
    defs = SubElement(root, "defs")
    radii = _numeric_sizes(viz.nodes, options.node_size_property, 4, 24, 7)
    widths = _numeric_sizes(viz.edges, options.edge_width_property, 0.5, 6, 1.2)
    markers = {color: f"arrow-{index}" for index, color in enumerate(PALETTE)}
    for color, marker_id in markers.items():
        marker = SubElement(defs, "marker", {"id": marker_id, "viewBox": "0 -5 10 10", "refX": "10",
                            "markerWidth": "7", "markerHeight": "7", "orient": "auto", "markerUnits": "userSpaceOnUse"})
        SubElement(marker, "path", {"d": "M0,-5L10,0L0,5", "fill": color})
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for edge in viz.edges:
        groups[tuple(sorted((edge.source, edge.target)))].append(edge)
    lanes: dict[str, float] = {}
    for group in groups.values():
        for index, edge in enumerate(sorted(group, key=lambda item: item.id)):
            lane = index + 1 if edge.source == edge.target else index - (len(group) - 1) / 2
            lanes[edge.id] = -lane if edge.source > edge.target else lane
    drawing = SubElement(root, "g")
    bounds: list[tuple[float, float]] = []
    label_layer: list[tuple[float, float, str, str]] = []
    for edge in viz.edges:
        x, y = coords[edge.source]
        tx, ty = coords[edge.target]
        lane = lanes[edge.id]
        radius, target_radius = radii[edge.source], radii[edge.target]
        if edge.source == edge.target:
            r = radius + 12 + lane * 12
            path = f"M {x} {y - radius} C {x + r * 2} {y - r * 2}, {x + r * 2} {y + r}, {x} {y + radius}"
            lx, ly = x + 1.5 * r, y - 0.375 * r
            bounds.extend(((x, y - radius), (x + r * 2, y - r * 2), (x + r * 2, y + r)))
        else:
            dx, dy = tx - x, ty - y
            distance = math.hypot(dx, dy) or 1
            cx, cy = (x + tx) / 2 - dy / distance * lane * 36, (y + ty) / 2 + dx / distance * lane * 36
            start, end = math.hypot(cx - x, cy - y) or 1, math.hypot(tx - cx, ty - cy) or 1
            sx, sy = x + (cx - x) / start * radius, y + (cy - y) / start * radius
            ex, ey = tx - (tx - cx) / end * (target_radius + 3), ty - (ty - cy) / end * (target_radius + 3)
            path = f"M {sx} {sy} Q {cx} {cy} {ex} {ey}"
            lx, ly = (sx + 2 * cx + ex) / 4, (sy + 2 * cy + ey) / 4
            bounds.extend(((sx, sy), (cx, cy), (ex, ey)))
        key = edge.type or "(untyped)"
        color = _color(_category(edge, options.edge_color_property, key))
        element = SubElement(drawing, "path", {"d": path, "fill": "none", "stroke": color,
                            "stroke-width": str(widths[edge.id]), "marker-end": f"url(#{markers[color]})"})
        SubElement(element, "title").text = f"{edge.id} ({key})"
        if options.edge_labels == "all":
            label_layer.append((lx, ly - 4, _label(edge, options.edge_label_property, edge.type or edge.id, options.label_max_length), "middle"))
    for node in viz.nodes:
        x, y = coords[node.id]
        radius = radii[node.id]
        bounds.extend(((x - radius - 1, y - radius - 1), (x + radius + 1, y + radius + 1)))
        element = SubElement(drawing, "circle", {"cx": str(x), "cy": str(y), "r": str(radius),
                            "fill": _color(_category(node, options.node_color_property, node.group or "(unlabeled)")), "stroke": fg, "stroke-width": "1.2"})
        SubElement(element, "title").text = node.id
        if options.show_labels:
            label_layer.append((x + radius + 3, y + 4, _label(node, options.node_label_property, node.id, options.label_max_length), "start"))
    for x, y, text, anchor in label_layer:
        # Conservative system-font bounds; no font or text-metrics dependency.
        text_width = len(text) * 10
        left = x - text_width / 2 if anchor == "middle" else x
        bounds.extend(((left - 2, y - 12), (left + text_width + 2, y + 4)))
        SubElement(drawing, "text", {"x": str(x), "y": str(y), "fill": fg, "stroke": bg,
                   "stroke-width": "3", "paint-order": "stroke", "text-anchor": anchor,
                   "font-family": "sans-serif", "font-size": "10"}).text = text
    if bounds:
        xs, ys = zip(*bounds)
        padding = min(20, width / 4, height / 4)
        footer = min(24, height / 4) if viz.truncation.truncated else 0
        scale = min((width - padding * 2) / max(max(xs) - min(xs), 1),
                    (height - padding * 2 - footer) / max(max(ys) - min(ys), 1), 1)
        tx = width / 2 - scale * (min(xs) + max(xs)) / 2
        ty = (height - footer) / 2 - scale * (min(ys) + max(ys)) / 2
        drawing.set("transform", f"translate({tx} {ty}) scale({scale})")
    if viz.truncation.truncated:
        SubElement(root, "text", {"x": "10", "y": str(height - 10), "fill": fg, "font-size": "12"}).text = (
            f"Capped graph: {viz.truncation.nodes_dropped} nodes / {viz.truncation.edges_dropped} edges dropped")
    return tostring(root, encoding="unicode")
