"""Typed-adjacency reads (Phase 3 extraction, read side).

These functions implement ``GraphDB``'s typed-traversal reads against an
explicit backend store, normalized ID bytes, and a caller-owned count cache.
They perform no writes and never touch graph records, indexes, or temporal
history.

``GraphDB.get/count/iter_typed_adjacency``, ``neighbors_by_edge_type``, and
``edges_by_edge_type`` remain the public API; they normalize IDs and delegate
here. The write side (``_put/_delete_typed_adjacency_for_edge``,
``rebuild_typed_adjacency``) stays on ``GraphDB`` until record/index
orchestration is extracted, because it is interleaved with ``put_edge``,
delete paths, and columnar ingestion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

_DIRECTIONS = ('out', 'in', 'any')


def _check_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise ValueError("direction must be 'out', 'in', or 'any'")


def iter_typed_adjacency(store: KVStore, node_id_bytes: bytes, edge_type: str,
                         direction: str = 'out'):
    """Yield typed adjacency records with clean direction semantics.

    Args:
        store: Backend store exposing ``iter_typed_adjacency``.
        node_id_bytes: Normalized node ID bytes.
        edge_type: Edge type to traverse.
        direction: ``"out"``, ``"in"``, or ``"any"``.

    Yields:
        Typed adjacency records containing edge, neighbor, source, target,
        edge type, and concrete direction fields.
    """
    _check_direction(direction)
    directions = ['out', 'in'] if direction == 'any' else [direction]
    for current_direction in directions:
        for edge_id, neighbor_id in store.iter_typed_adjacency(node_id_bytes, edge_type, current_direction):
            if current_direction == 'out':
                source_id = node_id_bytes
                target_id = neighbor_id
            else:
                source_id = neighbor_id
                target_id = node_id_bytes
            yield {
                'edge_id': edge_id,
                'neighbor_id': neighbor_id,
                'source_id': source_id,
                'target_id': target_id,
                'edge_type': edge_type,
                'direction': current_direction,
            }


def get_typed_adjacency(store: KVStore, node_id_bytes: bytes, edge_type: str,
                        direction: str = 'out') -> list:
    """Return typed adjacency records with clean direction semantics."""
    return list(iter_typed_adjacency(store, node_id_bytes, edge_type, direction))


def count_typed_adjacency(store: KVStore, cache: dict, node_id_bytes: bytes,
                          edge_type: str, direction: str = 'out') -> int:
    """Count typed adjacency records without materializing traversal records.

    ``cache`` maps ``(direction, node_id_bytes, edge_type)`` to counts and is
    owned by the calling graph handle (fresh per transaction/snapshot scope).
    """
    _check_direction(direction)
    if direction == 'any':
        return (count_typed_adjacency(store, cache, node_id_bytes, edge_type, 'out')
                + count_typed_adjacency(store, cache, node_id_bytes, edge_type, 'in'))

    cache_key = (direction, node_id_bytes, edge_type)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    count = store.count_typed_adjacency(node_id_bytes, edge_type, direction)
    cache[cache_key] = count
    return count


def neighbors_by_edge_type(store: KVStore, node_id_bytes: bytes, edge_type: str,
                           direction: str = 'out') -> list:
    """Return neighbor IDs connected by a specific edge type."""
    return [record['neighbor_id']
            for record in iter_typed_adjacency(store, node_id_bytes, edge_type, direction)]


def edges_by_edge_type(store: KVStore, node_id_bytes: bytes, edge_type: str,
                       direction: str = 'out') -> list:
    """Return edge IDs connected by a specific edge type."""
    return [record['edge_id']
            for record in get_typed_adjacency(store, node_id_bytes, edge_type, direction)]


def update_count_cache(cache: dict, records, delta: int) -> None:
    """Update cached typed-adjacency counts for writes made by one handle.

    ``cache`` is owned by the calling graph handle (fresh per
    transaction/snapshot scope). ``records`` are
    ``(source_id, target_id, edge_type, edge_id)`` tuples.
    """
    out_deltas: dict[tuple[str, bytes, str], int] = {}
    invalidated_in: set[tuple[str, bytes, str]] = set()
    for source_id, target_id, edge_type, _ in records:
        out_key = ("out", source_id, edge_type)
        out_deltas[out_key] = out_deltas.get(out_key, 0) + delta
        invalidated_in.add(("in", target_id, edge_type))
    for out_key, out_delta in out_deltas.items():
        current = cache.get(out_key, 0)
        cache[out_key] = max(0, current + out_delta)
    for in_key in invalidated_in:
        cache.pop(in_key, None)


def put_typed_adjacency_for_edge(store: KVStore, cache: dict, edge) -> None:
    """Write typed adjacency index records for an edge.

    Edges without ``properties["type"]`` are skipped, matching the
    long-standing write path.
    """
    from .entities import node_key_to_bytes

    edge_type = edge.get_type
    if edge_type is None:
        return
    source_id = node_key_to_bytes(edge.source)
    target_id = node_key_to_bytes(edge.target)
    store.put_typed_adjacency(source_id, target_id, edge_type, edge.get_id_bytes)
    update_count_cache(cache, [(source_id, target_id, edge_type, edge.get_id_bytes)], 1)


def delete_typed_adjacency_for_edge(store: KVStore, cache: dict, edge) -> None:
    """Remove typed adjacency index records for an edge."""
    from .entities import node_key_to_bytes

    edge_type = edge.get_type
    if edge_type is None:
        return
    source_id = node_key_to_bytes(edge.source)
    target_id = node_key_to_bytes(edge.target)
    store.delete_typed_adjacency(source_id, target_id, edge_type, edge.get_id_bytes)
    update_count_cache(cache, [(source_id, target_id, edge_type, edge.get_id_bytes)], -1)


def rebuild_typed_adjacency(store, entity_serializer, adj_cache) -> int:
    """Rebuild typed adjacency indexes from stored edge records.

    Returns the number of typed edges indexed. The caller owns the
    transaction boundary and clears/initializes the count cache.
    """
    from .edge_records import get_edge

    rebuilt = 0
    adj_cache.clear()
    for edge_id in store.get_edge_keys_generator():
        edge = get_edge(store, entity_serializer, edge_id)
        if edge is None or edge.get_type is None:
            continue
        put_typed_adjacency_for_edge(store, adj_cache, edge)
        rebuilt += 1
    return rebuilt
