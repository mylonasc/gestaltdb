"""Edge record orchestration (Phase 3 extraction, records side).

``put_edge``/``delete_edge`` coordinate canonical edge records, legacy
untyped adjacency blobs, typed adjacency, and secondary indexes. The
transaction boundary lives on ``EdgeRepository``/``StorageSession``; the
store-bound core stays here. Everything in this module
is explicit about its inputs — store, serializers, index configuration,
and the handle-owned typed-adjacency count cache — so transaction and
snapshot scopes bind correctly.

``EdgeRepository.update`` composes read/merge/write without changing the
legacy read-modify-write boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from ..kvstores import KVStore
    from ..serializers import Serializer
    from .entities import Edge, GraphEntityDictSerializer

from . import adjacency as _adjacency
from . import index_maintenance as _index_maintenance
from .entities import node_key_to_bytes


def get_edge(store: KVStore, entity_serializer: GraphEntityDictSerializer, edge_id: bytes) -> Edge | None:
    """Return an edge by byte key, or ``None`` when absent."""
    data = store.get_edge(edge_id)
    if data:
        return entity_serializer.deserialize(data, 'Edge')
    return None


def get_adjacency_list(store: KVStore, raw_serializer: Serializer, node_id: bytes,
                       direction: str = 'forward', return_raw: bool = False) -> Any:
    """Return the legacy adjacency list for a node ID.

    Returns ``[]`` when the node has no stored adjacency, the raw stored
    mapping when ``return_raw`` is set, and direction-filtered edge IDs
    otherwise.
    """
    raw = store.get_adjacency(node_id)
    if raw is None:
        return []
    adj = raw_serializer.deserialize(raw)

    if return_raw:
        return adj

    if direction == 'forward':
        return adj.get('target', [])
    if direction == 'backward':
        return adj.get('source', [])
    if direction == 'any':
        _s = adj.get('source', [])
        _t = adj.get('target', [])
        return list(set(_s).union(set(_t)))


def put_adjacency_list(store: KVStore, raw_serializer: Serializer, node_id: bytes, edges_list: dict[str, Any]) -> None:
    """Store the legacy adjacency list for a node ID."""
    raw = raw_serializer.serialize(edges_list)
    store.put_adjacency(node_id, raw)


def put_edge(store: KVStore, entity_serializer: GraphEntityDictSerializer, raw_serializer: Serializer,
             indexed_edge_properties: Iterable[str], adj_cache: dict[bytes, int], edge: Edge,
             update_adjacency: bool = True) -> None:
    """Store an edge and maintain adjacency and secondary indexes."""
    old_edge = get_edge(store, entity_serializer, edge.get_id_bytes)
    if old_edge is not None:
        _adjacency.delete_typed_adjacency_for_edge(store, adj_cache, old_edge)
        _index_maintenance.delete_edge_indexes(store, indexed_edge_properties, old_edge)
        remove_edge_from_endpoint_adjacencies(store, raw_serializer, old_edge)
    value = entity_serializer.serialize(edge, 'Edge')
    store.put_edge(edge.get_id_bytes, value)
    _adjacency.put_typed_adjacency_for_edge(store, adj_cache, edge)
    _index_maintenance.put_edge_indexes(store, indexed_edge_properties, edge)
    if update_adjacency:
        # Get the edge lists from the adjacency store,
        # and if they exist update them, if they don't exist
        # create them.
        for dict_flag, io_node in zip(['source', 'target'], (edge.source, edge.target)):
            io_node_key = node_key_to_bytes(cast(str, io_node))
            adj_list = store.get_adjacency(io_node_key)
            new_edge_list = {dict_flag: [edge.get_id]}
            if adj_list is None:
                serialized_adj_list = entity_serializer.serialize(new_edge_list, 'AdjacencyList')
                store.put_adjacency(io_node_key, serialized_adj_list)
            else:
                adj_edge_list = cast(dict[str, Any], entity_serializer.deserialize(adj_list, 'AdjacencyList'))
                if edge.get_id not in adj_edge_list.setdefault(dict_flag, []):
                    adj_edge_list[dict_flag].append(edge.get_id)
                store.put_adjacency(io_node_key, raw_serializer.serialize(adj_edge_list))


def delete_edge(store: KVStore, entity_serializer: GraphEntityDictSerializer, raw_serializer: Serializer,
                indexed_edge_properties: Iterable[str], adj_cache: dict[bytes, int], edge_id: bytes) -> None:
    """Remove an edge, its adjacency records, and its secondary indexes."""
    e = get_edge(store, entity_serializer, edge_id)
    if not e:
        return  # Edge not found

    _adjacency.delete_typed_adjacency_for_edge(store, adj_cache, e)
    _index_maintenance.delete_edge_indexes(store, indexed_edge_properties, e)
    remove_edge_from_endpoint_adjacencies(store, raw_serializer, e)

    store.delete_edge(edge_id)


def edge_id_variants(edge_id: str | bytes) -> list[str | bytes]:
    """Return string/byte forms used by legacy adjacency records."""
    variants = [edge_id]
    if isinstance(edge_id, bytes):
        try:
            edge_id_text = edge_id.decode("utf-8")
        except UnicodeDecodeError:
            edge_id_text = None
        if edge_id_text is not None:
            variants.append(edge_id_text)
    elif isinstance(edge_id, str):
        variants.append(edge_id.encode("utf-8"))
    return variants


def remove_edge_from_endpoint_adjacencies(store: KVStore, raw_serializer: Serializer, edge: Edge) -> None:
    """Remove an edge from the legacy adjacency blobs for its endpoints."""
    source_id = node_key_to_bytes(cast(str, edge.source))
    target_id = node_key_to_bytes(cast(str, edge.target))
    remove_edge_from_adjacency(store, raw_serializer, source_id, edge.get_id)
    remove_edge_from_adjacency(store, raw_serializer, source_id, edge.get_id_bytes)
    if target_id != source_id:
        remove_edge_from_adjacency(store, raw_serializer, target_id, edge.get_id)
        remove_edge_from_adjacency(store, raw_serializer, target_id, edge.get_id_bytes)


def remove_edge_from_adjacency(store: KVStore, raw_serializer: Serializer, node_id: bytes, edge_id: str | bytes) -> None:
    """Remove an edge ID from a node's stored adjacency lists."""
    adj_list = get_adjacency_list(store, raw_serializer, node_id, return_raw=True)
    _changed = False
    edge_id_variants_set = set(edge_id_variants(edge_id))
    for _dir in ['source', 'target']:
        if _dir in adj_list:
            filtered_edges = [existing_edge_id for existing_edge_id in adj_list[_dir] if existing_edge_id not in edge_id_variants_set]
            if len(filtered_edges) != len(adj_list[_dir]):
                adj_list[_dir] = filtered_edges
                _changed = True
    if _changed:
        if not adj_list.get('source') and not adj_list.get('target'):
            store.delete_adjacency(node_id)
        else:
            put_adjacency_list(store, raw_serializer, node_id, adj_list)


def bfs(store: KVStore, entity_serializer: GraphEntityDictSerializer, raw_serializer: Serializer, start_node_id: bytes,
        direction: str = 'any', edge_key_serializer: Callable[[Any], bytes] = lambda x: cast(str, x).encode('utf-8'),
        node_key_serializer: Callable[[Any], bytes] = lambda x: cast(str, x).encode('utf-8')) -> list[bytes]:
    """Return node IDs in BFS order over legacy adjacency blobs."""
    visited: set[bytes] = set()
    queue: list[bytes] = [start_node_id]
    result: list[bytes] = []
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        result.append(current)

        # 1) get adjacency list for current
        edges_list = get_adjacency_list(store, raw_serializer, current, direction=direction)

        # 2) for each edge in adjacency, find the other node
        for e_id in edges_list:
            _ser_edge_key = edge_key_serializer(e_id)
            edge_obj = get_edge(store, entity_serializer, _ser_edge_key)
            if not edge_obj:
                continue
            _source_node = node_key_serializer(edge_obj.source)
            neighbor = (
                node_key_serializer(edge_obj.target) if _source_node == current else _source_node
            )
            if neighbor not in visited:
                queue.append(neighbor)

    return result
