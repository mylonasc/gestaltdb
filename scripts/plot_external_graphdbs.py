#!/usr/bin/env python3
"""Generate static SVG plots from external graph database benchmark summaries."""

from __future__ import annotations

import argparse
from html import escape
import json
from math import log10
from pathlib import Path


ENGINE_LABELS = {
    "gestaltdb-rocksdb": "GestaltDB",
    "gestaltdb-rocksdb-transactional": "GestaltDB tx",
    "apache-age": "Apache AGE",
    "arcadedb-embedded": "ArcadeDB",
    "memgraph": "Memgraph",
    "neo4j": "Neo4j",
}
ENGINE_ORDER = ["gestaltdb-rocksdb", "gestaltdb-rocksdb-transactional", "apache-age", "arcadedb-embedded", "memgraph", "neo4j"]
WORKLOAD_ORDER = ["neighbors", "sample_neighbors", "typed_path", "deep_typed_query", "bfs_depth", "star_traversal"]
WORKLOAD_LABELS = {
    "neighbors": "neighbors",
    "sample_neighbors": "sample neighbors",
    "typed_path": "typed path",
    "deep_typed_query": "deep typed",
    "bfs_depth": "BFS depth",
    "star_traversal": "star traversal",
}


def read_summary(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_rows(paths: list[Path]) -> list[dict[str, object]]:
    rows_by_key: dict[tuple[str, str], dict[str, object]] = {}
    for path in paths:
        for row in read_summary(path):
            if row.get("status") != "ok":
                continue
            rows_by_key[(str(row["engine"]), str(row["workload"]))] = row
    return list(rows_by_key.values())


def fmt_seconds(value: float) -> str:
    if value >= 10:
        return f"{value:.1f}s"
    if value >= 1:
        return f"{value:.2f}s"
    if value >= 0.01:
        return f"{value * 1000:.0f}ms"
    return f"{value * 1000:.1f}ms"


def write_ingest_svg(rows: list[dict[str, object]], output: Path) -> None:
    ingest_rows = [row for row in rows if row.get("workload") == "columnar_ingest" and row.get("engine") in ENGINE_ORDER]
    ingest_rows.sort(key=lambda row: float(row["ingest_seconds_mean"]))
    width = 940
    left = 220
    right = 160
    top = 72
    row_h = 46
    height = top + len(ingest_rows) * row_h + 56
    max_seconds = max(float(row["ingest_seconds_mean"]) for row in ingest_rows)
    bar_width = width - left - right
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">External graph database ingestion time</title>',
        '<desc id="desc">Mean ingestion time for a 100000 node and 500000 edge benchmark, lower is better.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="34" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700" fill="#111827">Ingestion time: 100k nodes / 500k edges</text>',
        '<text x="24" y="56" font-family="Inter, Arial, sans-serif" font-size="13" fill="#4b5563">Mean seconds over 3 repetitions. AGE includes CSV bulk load plus GIN index build.</text>',
    ]
    for index, row in enumerate(ingest_rows):
        y = top + index * row_h
        seconds = float(row["ingest_seconds_mean"])
        std = float(row.get("ingest_seconds_std") or 0.0)
        rate = float(row["edges_per_second_mean"])
        bar = seconds / max_seconds * bar_width
        engine = ENGINE_LABELS.get(str(row["engine"]), str(row["engine"]))
        fill = "#2563eb" if row["engine"] == "gestaltdb-rocksdb" else "#7c3aed" if row["engine"] == "apache-age" else "#94a3b8"
        lines.extend(
            [
                f'<text x="24" y="{y + 27}" font-family="Inter, Arial, sans-serif" font-size="14" fill="#111827">{escape(engine)}</text>',
                f'<rect x="{left}" y="{y + 8}" width="{bar:.1f}" height="24" rx="4" fill="{fill}"/>',
                f'<text x="{left + bar + 10:.1f}" y="{y + 26}" font-family="Inter, Arial, sans-serif" font-size="13" fill="#111827">{seconds:.3f}s +/- {std:.3f}s</text>',
                f'<text x="{width - 145}" y="{y + 26}" font-family="Inter, Arial, sans-serif" font-size="12" fill="#4b5563">{rate:,.0f} edges/s</text>',
            ]
        )
    lines.append('</svg>')
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def color_for(value: float, minimum: float, maximum: float) -> str:
    if maximum <= minimum:
        ratio = 0.0
    else:
        ratio = (log10(value) - minimum) / (maximum - minimum)
    ratio = max(0.0, min(1.0, ratio))
    r1, g1, b1 = 219, 234, 254
    r2, g2, b2 = 124, 58, 237
    r = int(r1 + (r2 - r1) * ratio)
    g = int(g1 + (g2 - g1) * ratio)
    b = int(b1 + (b2 - b1) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def write_query_svg(rows: list[dict[str, object]], output: Path) -> None:
    values: dict[tuple[str, str], float] = {}
    for row in rows:
        engine = str(row.get("engine"))
        workload = str(row.get("workload"))
        if engine in ENGINE_ORDER and workload in WORKLOAD_ORDER:
            values[(engine, workload)] = float(row["query_seconds_mean"])
    logs = [log10(value) for value in values.values() if value > 0]
    min_log = min(logs)
    max_log = max(logs)
    left = 156
    top = 86
    cell_w = 122
    cell_h = 48
    width = left + len(ENGINE_ORDER) * cell_w + 34
    height = top + len(WORKLOAD_ORDER) * cell_h + 68
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">External graph database query latency heatmap</title>',
        '<desc id="desc">Mean query time by workload and database engine on a log color scale, lower is better.</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="24" y="34" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700" fill="#111827">Query time by workload</text>',
        '<text x="24" y="56" font-family="Inter, Arial, sans-serif" font-size="13" fill="#4b5563">Mean seconds over 3 repetitions. Color uses log scale; lighter cells are faster.</text>',
    ]
    for col, engine in enumerate(ENGINE_ORDER):
        x = left + col * cell_w + cell_w / 2
        label = ENGINE_LABELS.get(engine, engine)
        lines.append(f'<text x="{x:.1f}" y="78" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="12" fill="#111827">{escape(label)}</text>')
    for row_index, workload in enumerate(WORKLOAD_ORDER):
        y = top + row_index * cell_h
        label = WORKLOAD_LABELS[workload]
        lines.append(f'<text x="24" y="{y + 30}" font-family="Inter, Arial, sans-serif" font-size="13" fill="#111827">{escape(label)}</text>')
        for col, engine in enumerate(ENGINE_ORDER):
            x = left + col * cell_w
            value = values.get((engine, workload))
            if value is None:
                lines.extend(
                    [
                        f'<rect x="{x}" y="{y}" width="{cell_w - 6}" height="{cell_h - 6}" rx="6" fill="#f3f4f6"/>',
                        f'<text x="{x + (cell_w - 6) / 2:.1f}" y="{y + 27}" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="12" fill="#6b7280">n/a</text>',
                    ]
                )
                continue
            fill = color_for(value, min_log, max_log)
            text_fill = "#ffffff" if (log10(value) - min_log) / (max_log - min_log) > 0.62 else "#111827"
            lines.extend(
                [
                    f'<rect x="{x}" y="{y}" width="{cell_w - 6}" height="{cell_h - 6}" rx="6" fill="{fill}"/>',
                    f'<text x="{x + (cell_w - 6) / 2:.1f}" y="{y + 27}" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="12" font-weight="600" fill="{text_fill}">{fmt_seconds(value)}</text>',
                ]
            )
    lines.append('<text x="24" y="{0}" font-family="Inter, Arial, sans-serif" font-size="12" fill="#4b5563">Lower is better. Star traversal returns 5,000,000 rows; other workloads return small seeded traversals.</text>'.format(height - 24))
    lines.append('</svg>')
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/_static"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.summaries)
    write_ingest_svg(rows, args.output_dir / "external_graphdb_ingest_100k.svg")
    write_query_svg(rows, args.output_dir / "external_graphdb_queries_100k.svg")


if __name__ == "__main__":
    main()
