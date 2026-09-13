"""Synthetic datasets, deterministic graph shapes, and data batching utilities."""

from __future__ import annotations

import random
from typing import Any, Iterable, Sequence

from gestaltdb.graphdb import Edge, Node

DEFAULT_EDGE_TYPES: tuple[str, ...] = ("rel-a", "rel-b", "rel-c")
NAMED_EDGE_TYPES: tuple[str, ...] = ("RelA", "RelB", "RelC")
BIO_EDGE_TYPES: tuple[str, ...] = ("drug-to-protein", "protein-to-disease", "drug-to-disease")


def chunks(total: int, chunk_size: int) -> Iterable[tuple[int, int]]:
    """Yield ``(start, end)`` index pairs for a total range."""
    for start in range(0, total, chunk_size):
        yield start, min(start + chunk_size, total)


def chunk_items(items: Sequence[Any], chunk_size: int) -> Iterable[Sequence[Any]]:
    """Yield slices of length ``chunk_size`` from a sequence."""
    for start in range(0, len(items), chunk_size):
        yield items[start : start + chunk_size]


def workload_graph_shape(workload: str, override_shape: str = "auto") -> str:
    """Map a workload name to its characteristic graph shape."""
    if override_shape != "auto":
        return override_shape
    if workload == "star_traversal":
        return "star"
    if workload == "typed_path":
        return "typed_path"
    return "synthetic"


def edge_parts(
    index: int,
    nodes: int,
    graph_shape: str = "synthetic",
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
) -> tuple[str, str, str, str]:
    """Return ``(edge_id, source, target, edge_type)`` for a given index and graph shape."""
    edge_id = f"e{index}"
    if graph_shape == "star":
        target = 1 + (index % max(1, nodes - 1))
        return edge_id, "n0", f"n{target}", edge_types[0]

    if graph_shape == "typed_path":
        source = f"n{index % nodes}"
        target = f"n{(index % nodes + 1) % nodes}"
        edge_type = edge_types[index % min(2, len(edge_types))]
        return edge_id, source, target, edge_type

    if graph_shape == "layered":
        source_idx = index % nodes
        step = (index % 31) + 1
        target_idx = (source_idx + step) % nodes
        edge_type = edge_types[index % len(edge_types)]
        return edge_id, f"n{source_idx}", f"n{target_idx}", edge_type

    # Default synthetic shape
    source = f"n{index % nodes}"
    target = f"n{(index * 9973 + 1) % nodes}"
    edge_type = edge_types[index % len(edge_types)]
    return edge_id, source, target, edge_type


def make_node(
    index: int,
    label: str = "Node",
    properties: dict[str, Any] | None = None,
) -> Node:
    """Create a deterministic synthetic Node."""
    props = {"group": index % 128} if properties is None else dict(properties)
    return Node(node_id=f"n{index}", labels=(label,), properties=props)


def make_edge(
    index: int,
    nodes: int,
    graph_shape: str = "synthetic",
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
    properties: dict[str, Any] | None = None,
) -> Edge:
    """Create a deterministic synthetic Edge."""
    edge_id, source, target, edge_type = edge_parts(index, nodes, graph_shape, edge_types)
    props = {"type": edge_type, "weight": index % 1000}
    if properties:
        props.update(properties)
    return Edge(edge_id=edge_id, source=source, target=target, properties=props)


def seed_ids(count: int, max_nodes: int | None = None) -> list[str]:
    """Return a list of deterministic seed node IDs."""
    n = min(count, max_nodes) if max_nodes is not None else count
    return [f"n{i}" for i in range(n)]


def node_rows(start: int, end: int, label: str = "Node") -> list[dict[str, Any]]:
    """Return list of dictionary records for tabular/columnar node ingestion."""
    return [
        {
            "node_id": f"n{index}",
            "labels": [label],
            "id": f"n{index}",
            "group": index % 128,
        }
        for index in range(start, end)
    ]


def edge_rows(
    start: int,
    end: int,
    nodes: int,
    graph_shape: str = "synthetic",
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
) -> list[dict[str, Any]]:
    """Return list of dictionary records for tabular/columnar edge ingestion."""
    rows: list[dict[str, Any]] = []
    for index in range(start, end):
        edge_id, source, target, edge_type = edge_parts(index, nodes, graph_shape, edge_types)
        rows.append({
            "edge_id": edge_id,
            "id": edge_id,
            "source": source,
            "target": target,
            "edge_type": edge_type,
            "type": edge_type,
            "weight": index % 1000,
        })
    return rows


def edge_rows_by_type(
    start: int,
    end: int,
    nodes: int,
    graph_shape: str = "synthetic",
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
) -> dict[str, list[dict[str, Any]]]:
    """Return edge rows partitioned by edge type."""
    by_type: dict[str, list[dict[str, Any]]] = {et: [] for et in edge_types}
    for row in edge_rows(start, end, nodes, graph_shape, edge_types):
        by_type.setdefault(str(row["edge_type"]), []).append(row)
    return by_type


def pyarrow_array(values: Sequence[Any]) -> Any:
    """Create a PyArrow array from a sequence of values."""
    try:
        import pyarrow as pa
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for arrow benchmarks") from exc
    return pa.array(values)


def polars_frame(data: dict[str, list[Any]]) -> Any:
    """Create a Polars DataFrame from a dictionary of columns."""
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError("polars is required for polars benchmarks") from exc
    return pl.DataFrame(data)
