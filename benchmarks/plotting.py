"""Generate static SVG plots from external graph database benchmark summaries."""

from __future__ import annotations

import argparse
from html import escape
import json
from math import log10
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

ENGINE_LABELS: dict[str, str] = {
    "gestaltdb-rocksdb": "GestaltDB",
    "gestaltdb-rocksdb-transactional": "GestaltDB tx",
    "apache-age": "Apache AGE",
    "arcadedb-embedded": "ArcadeDB",
    "memgraph": "Memgraph",
    "neo4j": "Neo4j",
}
ENGINE_ORDER = ["gestaltdb-rocksdb", "gestaltdb-rocksdb-transactional", "apache-age", "arcadedb-embedded", "memgraph", "neo4j"]
WORKLOAD_ORDER = ["neighbors", "sample_neighbors", "typed_path", "deep_typed_query", "bfs_depth", "star_traversal"]
WORKLOAD_LABELS: dict[str, str] = {
    "neighbors": "neighbors",
    "sample_neighbors": "sample neighbors",
    "typed_path": "typed path",
    "deep_typed_query": "deep typed",
    "bfs_depth": "BFS depth",
    "star_traversal": "star traversal",
}


def read_summary(path: Path) -> list[dict[str, Any]]:
    """Read JSONL benchmark summary file."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    """Load and merge benchmark rows from multiple summary files."""
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for row in read_summary(path):
            if row.get("status") != "ok":
                continue
            rows_by_key[(str(row["engine"]), str(row["workload"]))] = row
    return list(rows_by_key.values())


def fmt_seconds(value: float) -> str:
    """Format elapsed seconds cleanly."""
    if value >= 10:
        return f"{value:.1f}s"
    if value >= 1:
        return f"{value:.2f}s"
    if value >= 0.01:
        return f"{value * 1000:.0f}ms"
    return f"{value * 1000:.1f}ms"


def write_ingest_svg(rows: list[dict[str, Any]], output: Path) -> None:
    """Generate SVG bar chart for ingestion time comparison."""
    ingest_rows = [row for row in rows if row.get("workload") == "columnar_ingest" and row.get("engine") in ENGINE_ORDER]
    ingest_rows.sort(key=lambda row: float(row["ingest_seconds_mean"]))
    if not ingest_rows:
        return

    baseline = float(ingest_rows[0]["ingest_seconds_mean"])
    max_seconds = max(float(row["ingest_seconds_mean"]) for row in ingest_rows)
    bar_max_width = 380
    row_height = 42
    top = 60
    height = top + len(ingest_rows) * row_height + 30
    width = 680

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "  text { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }",
        "  .title { font-size: 16px; font-weight: 600; fill: #1f2328; }",
        "  .subtitle { font-size: 12px; fill: #656d76; }",
        "  .label { font-size: 13px; fill: #1f2328; text-anchor: end; }",
        "  .bar { fill: #0969da; rx: 4px; }",
        "  .bar-baseline { fill: #1a7f37; rx: 4px; }",
        "  .value { font-size: 12px; fill: #1f2328; font-variant-numeric: tabular-nums; }",
        "  .relative { font-size: 11px; fill: #656d76; }",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff" />',
        '<text x="24" y="28" class="title">100k Node / 500k Edge Ingestion Time</text>',
        '<text x="24" y="46" class="subtitle">Lower is faster · Mean wall-clock seconds across 3 runs</text>',
    ]

    for index, row in enumerate(ingest_rows):
        y = top + index * row_height
        engine = str(row["engine"])
        mean_sec = float(row["ingest_seconds_mean"])
        std_sec = float(row.get("ingest_seconds_std") or 0.0)
        rel = mean_sec / baseline
        bar_w = max(4.0, (mean_sec / max_seconds) * bar_max_width)
        bar_cls = "bar-baseline" if index == 0 else "bar"
        label = ENGINE_LABELS.get(engine, engine)

        lines.extend([
            f'<text x="170" y="{y + 20}" class="label">{escape(label)}</text>',
            f'<rect x="180" y="{y + 6}" width="{bar_w:.1f}" height="22" class="{bar_cls}" />',
            f'<text x="{190 + bar_w:.1f}" y="{y + 21}" class="value">{mean_sec:.2f}s ±{std_sec:.2f}s</text>',
            f'<text x="{330 + bar_w:.1f}" y="{y + 21}" class="relative">({rel:.1f}x)</text>',
        ])

    lines.append("</svg>\n")
    output.write_text("\n".join(lines), encoding="utf-8")


def write_heatmap_svg(rows: list[dict[str, Any]], output: Path) -> None:
    """Generate SVG heatmap for query latency comparison."""
    matrix: dict[tuple[str, str], float] = {}
    for row in rows:
        engine = str(row.get("engine"))
        workload = str(row.get("workload"))
        if engine in ENGINE_ORDER and workload in WORKLOAD_ORDER:
            sec = row.get("query_seconds_mean")
            if isinstance(sec, (int, float)):
                matrix[(engine, workload)] = float(sec)

    if not matrix:
        return

    workload_mins = {
        workload: min((matrix.get((eng, workload), float("inf")) for eng in ENGINE_ORDER), default=1.0)
        for workload in WORKLOAD_ORDER
    }
    workload_maxs = {
        workload: max((matrix.get((eng, workload), 0.0) for eng in ENGINE_ORDER), default=1.0)
        for workload in WORKLOAD_ORDER
    }

    margin_left = 130
    margin_top = 80
    cell_w = 90
    cell_h = 46
    width = margin_left + len(ENGINE_ORDER) * cell_w + 30
    height = margin_top + len(WORKLOAD_ORDER) * cell_h + 40

    def cell_color(val: float, w_min: float, w_max: float) -> tuple[str, str]:
        if w_max <= w_min:
            norm = 0.0
        else:
            log_val = log10(max(val, 1e-6))
            log_min = log10(max(w_min, 1e-6))
            log_max = log10(max(w_max, 1e-6))
            norm = (log_val - log_min) / (log_max - log_min) if log_max > log_min else 0.0
        norm = max(0.0, min(1.0, norm))
        red = int(245 + norm * (180 - 245))
        green = int(245 - norm * 150)
        blue = int(245 - norm * 180)
        text_color = "#ffffff" if norm > 0.6 else "#1f2328"
        return f"rgb({red},{green},{blue})", text_color

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "  text { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }",
        "  .title { font-size: 16px; font-weight: 600; fill: #1f2328; }",
        "  .subtitle { font-size: 12px; fill: #656d76; }",
        "  .col-label { font-size: 12px; font-weight: 600; fill: #1f2328; text-anchor: middle; }",
        "  .row-label { font-size: 12px; fill: #1f2328; text-anchor: end; }",
        "  .cell-text { font-size: 11px; font-weight: 600; text-anchor: middle; font-variant-numeric: tabular-nums; }",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff" />',
        '<text x="24" y="28" class="title">Query Latency Heatmap (Mean Seconds)</text>',
        '<text x="24" y="46" class="subtitle">Green is faster relative to row min · Star traversal materializes 5M records</text>',
    ]

    for c_idx, engine in enumerate(ENGINE_ORDER):
        x = margin_left + c_idx * cell_w + cell_w / 2
        lines.append(f'<text x="{x}" y="{margin_top - 12}" class="col-label">{escape(ENGINE_LABELS.get(engine, engine))}</text>')

    for r_idx, workload in enumerate(WORKLOAD_ORDER):
        y = margin_top + r_idx * cell_h
        lines.append(f'<text x="{margin_left - 12}" y="{y + 28}" class="row-label">{escape(WORKLOAD_LABELS.get(workload, workload))}</text>')
        for c_idx, engine in enumerate(ENGINE_ORDER):
            x = margin_left + c_idx * cell_w
            val = matrix.get((engine, workload))
            if val is None:
                continue
            bg, fg = cell_color(val, workload_mins[workload], workload_maxs[workload])
            lines.extend([
                f'<rect x="{x + 2}" y="{y + 2}" width="{cell_w - 4}" height="{cell_h - 4}" rx="4" fill="{bg}" />',
                f'<text x="{x + cell_w / 2}" y="{y + 28}" class="cell-text" fill="{fg}">{fmt_seconds(val)}</text>',
            ])

    lines.append("</svg>\n")
    output.write_text("\n".join(lines), encoding="utf-8")


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build argument parser for plot generation."""
    parser = subparser or argparse.ArgumentParser(description="Generate SVG plots from external graphdb summary JSONL files")
    parser.add_argument("summary_files", nargs="+", type=Path, help="Paths to summary JSONL files")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/_static"), help="Output directory for SVG files")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    rows = load_rows(args.summary_files)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ingest_path = args.output_dir / "external_graphdb_ingest_100k.svg"
    heatmap_path = args.output_dir / "external_graphdb_queries_100k.svg"
    write_ingest_svg(rows, ingest_path)
    write_heatmap_svg(rows, heatmap_path)
    print(f"Generated plots:\n  {ingest_path}\n  {heatmap_path}")


if __name__ == "__main__":
    main()
