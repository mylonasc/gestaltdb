"""Benchmark result reporting, summary statistics, and disk writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import platform
import statistics
import sys
from typing import Any, Iterable, Sequence


def base_row(
    engine: str,
    workload: str,
    args: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a normalized benchmark result dictionary."""
    row: dict[str, Any] = {
        "status": "ok",
        "skip_reason": "",
        "engine": engine,
        "workload": workload,
        "repetition": getattr(args, "repetition", ""),
        "nodes": getattr(args, "nodes", 0),
        "edges": getattr(args, "edges", 0),
        "iterations": getattr(args, "iterations", 0),
        "batch_size": getattr(args, "batch_size", 0),
        "graph_shape": getattr(args, "graph_shape", "synthetic"),
        "sample_size": getattr(args, "sample_size", 0),
        "depth": getattr(args, "depth", 0),
        "bfs_limit": getattr(args, "bfs_limit", 0),
        "ingest_seconds": "",
        "query_seconds": "",
        "reopen_seconds": "",
        "count_seconds": "",
        "total_seconds": "",
        "nodes_per_second": "",
        "edges_per_second": "",
        "queries_per_second": "",
        "result_count": "",
        "actual_nodes": "",
        "actual_edges": "",
        "count_status": "",
        "db_bytes": "",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    if extra:
        row.update(extra)
    return row


def add_rates(
    row: dict[str, Any],
    nodes: int | None = None,
    edges: int | None = None,
) -> None:
    """Compute and record node, edge, query, and traversal rates."""
    num_nodes = nodes if nodes is not None else int(row.get("nodes") or 0)
    num_edges = edges if edges is not None else int(row.get("edges") or 0)
    iterations = int(row.get("iterations") or 0)

    ingest_sec = row.get("ingest_seconds")
    query_sec = row.get("query_seconds")
    total_sec = 0.0

    if isinstance(ingest_sec, (int, float)):
        total_sec += ingest_sec
        if ingest_sec > 0:
            if num_nodes > 0:
                row["nodes_per_second"] = num_nodes / ingest_sec
            if num_edges > 0:
                row["edges_per_second"] = num_edges / ingest_sec

    if isinstance(query_sec, (int, float)):
        total_sec += query_sec
        if query_sec > 0 and iterations > 0:
            row["queries_per_second"] = iterations / query_sec

    if total_sec > 0:
        row["total_seconds"] = total_sec


def count_status(row: dict[str, Any], expected_nodes: int, expected_edges: int) -> str:
    """Validate loaded entity counts against expected totals."""
    if row.get("actual_nodes") == expected_nodes and row.get("actual_edges") == expected_edges:
        return "ok"
    return "mismatch"


def summarize_rows(
    rows: list[dict[str, Any]],
    group_fields: Sequence[str] = ("engine", "workload"),
    metric_fields: Sequence[str] = (
        "ingest_seconds",
        "query_seconds",
        "reopen_seconds",
        "count_seconds",
        "total_seconds",
        "nodes_per_second",
        "edges_per_second",
        "queries_per_second",
        "result_count",
        "actual_nodes",
        "actual_edges",
        "db_bytes",
    ),
) -> list[dict[str, Any]]:
    """Group rows by key fields and compute mean and standard deviation for metrics."""
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in group_fields)
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for key, group in grouped.items():
        first = group[0]
        summary: dict[str, Any] = {field: first.get(field, "") for field in group_fields}
        summary["status"] = "ok" if any(r.get("status") == "ok" for r in group) else first.get("status", "")
        summary["runs"] = len(group)
        for passthrough in (
            "nodes",
            "edges",
            "iterations",
            "batch_size",
            "graph_shape",
            "sample_size",
            "depth",
            "skip_reason",
        ):
            if passthrough in first:
                summary[passthrough] = first[passthrough]

        for metric in metric_fields:
            vals = [float(r[metric]) for r in group if isinstance(r.get(metric), (int, float))]
            if vals:
                summary[f"{metric}_mean"] = statistics.mean(vals)
                summary[f"{metric}_std"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
            else:
                summary[f"{metric}_mean"] = ""
                summary[f"{metric}_std"] = ""
        summary_rows.append(summary)

    return summary_rows


class ResultWriter:
    """Stream benchmark rows to CSV and JSONL files."""

    def __init__(
        self,
        output_dir: Path | str,
        csv_filename: str = "results.csv",
        jsonl_filename: str = "results.jsonl",
        fieldnames: Sequence[str] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.output_dir / csv_filename
        self.jsonl_path = self.output_dir / jsonl_filename
        self.fieldnames = list(fieldnames) if fieldnames else None

    def write_row(self, row: dict[str, Any]) -> None:
        """Append a single row to CSV and JSONL."""
        with self.jsonl_path.open("a", encoding="utf-8") as jsonl_file:
            jsonl_file.write(json.dumps(row, sort_keys=True) + "\n")

        write_header = not self.csv_path.exists()
        fields = self.fieldnames or list(row.keys())
        with self.csv_path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    def write_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        """Append multiple rows."""
        for row in rows:
            self.write_row(row)
