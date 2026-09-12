"""Standard graph query and traversal workloads for GestaltDB benchmarks."""

from __future__ import annotations

from collections import deque
import random
from typing import Sequence

from gestaltdb.graphdb import GraphDB
from .datasets import NAMED_EDGE_TYPES
from .timing import reservoir_count


def count_gestaltdb(
    graph: GraphDB,
    label: str = "Node",
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
) -> tuple[int, int]:
    """Count nodes and typed edges in GestaltDB."""
    nodes = graph.count_nodes_by_label(label)
    edges = sum(graph.count_edges_by_type(edge_type) for edge_type in edge_types)
    return nodes, edges


def gestaltdb_bfs(
    graph: GraphDB,
    start_node: str,
    depth: int | None = None,
    limit: int = 100_000,
    edge_types: Sequence[str] = NAMED_EDGE_TYPES,
) -> int:
    """Perform breadth-first search traversal over typed adjacency records."""
    visited: set[bytes] = set()
    queue = deque([(graph.node_key_to_bytes(start_node), 0)])
    while queue and len(visited) <= limit:
        current, current_depth = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if depth is not None and current_depth >= depth:
            continue
        for edge_type in edge_types:
            for record in graph.iter_typed_adjacency(current, edge_type, direction="out"):
                neighbor = record["neighbor_id"]
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
    return min(max(0, len(visited) - 1), limit)


def gestaltdb_neighbors(
    graph: GraphDB,
    seed_ids: Sequence[str],
    edge_type: str = "RelA",
) -> int:
    """Expand outgoing neighbors for a list of seed nodes."""
    total = 0
    for seed in seed_ids:
        seed_bytes = graph.node_key_to_bytes(seed)
        total += sum(1 for _ in graph.iter_typed_adjacency(seed_bytes, edge_type, direction="out"))
    return total


def gestaltdb_sample_neighbors(
    graph: GraphDB,
    seed_ids: Sequence[str],
    edge_type: str = "RelA",
    sample_size: int = 5,
    rng: random.Random | None = None,
) -> int:
    """Sample outgoing neighbors for a list of seed nodes using reservoir sampling."""
    random_gen = rng if rng is not None else random.Random(42)
    total = 0
    for seed in seed_ids:
        seed_bytes = graph.node_key_to_bytes(seed)
        records = graph.iter_typed_adjacency(seed_bytes, edge_type, direction="out")
        total += reservoir_count(records, sample_size, random_gen)
    return total


def gestaltdb_star_traversal(
    graph: GraphDB,
    seed_id: str = "n0",
    edge_type: str = "RelA",
    iterations: int = 1,
) -> int:
    """Traverse a high-degree star hub repeatedly."""
    total = 0
    seed_bytes = graph.node_key_to_bytes(seed_id)
    for _ in range(iterations):
        total += sum(1 for _ in graph.iter_typed_adjacency(seed_bytes, edge_type, direction="out"))
    return total


def gestaltdb_typed_path(
    graph: GraphDB,
    seed_ids: Sequence[str],
    edge_types: tuple[str, str] = ("RelA", "RelB"),
    fanout_limit: int = 10_000,
) -> int:
    """Traverse 2-hop typed paths: (seed)-[RelA]->(mid)-[RelB]->(dest)."""
    total = 0
    max_total = fanout_limit * len(seed_ids)
    first_type, second_type = edge_types
    for seed in seed_ids:
        seed_bytes = graph.node_key_to_bytes(seed)
        first_hop = graph.iter_typed_adjacency(seed_bytes, first_type, direction="out")
        for edge in first_hop:
            mid = edge["neighbor_id"]
            total += sum(1 for _ in graph.iter_typed_adjacency(mid, second_type, direction="out"))
            if total >= max_total:
                break
        if total >= max_total:
            break
    return total


def gestaltdb_deep_typed_query(
    graph: GraphDB,
    seed_ids: Sequence[str],
    fanout_limit: int = 10_000,
) -> int:
    """Traverse 3-hop typed path: (seed)-[RelA]->(mid1)-[RelB]->(mid2)-[RelC]->(dest)."""
    total = 0
    max_total = fanout_limit * len(seed_ids)
    for seed in seed_ids:
        seed_bytes = graph.node_key_to_bytes(seed)
        hop1 = graph.iter_typed_adjacency(seed_bytes, "RelA", direction="out")
        for e1 in hop1:
            hop2 = graph.iter_typed_adjacency(e1["neighbor_id"], "RelB", direction="out")
            for e2 in hop2:
                total += sum(1 for _ in graph.iter_typed_adjacency(e2["neighbor_id"], "RelC", direction="out"))
                if total >= max_total:
                    break
            if total >= max_total:
                break
        if total >= max_total:
            break
    return total
