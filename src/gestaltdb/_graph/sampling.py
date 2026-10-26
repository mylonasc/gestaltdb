"""Live typed-traversal sampling (Phase 3 extraction, sampling side).

Reservoir sampling over typed adjacency and seed nodes, plus ordered path
and subgraph materialization. These functions operate on an explicit store
(plus entity decoding and the freshness guard for label-index scans);
``GraphDB`` keeps the public signatures, validation order, and docstrings
and delegates here.

Snapshot building (``build_sampler_snapshot``) stays on ``GraphDB``: it
orchestrates read views, provenance capture, and manifest references.
Array-native sampling stays in ``gestaltdb.sampling``.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..sampling import RelationHierarchy, as_sampling_pattern
from . import adjacency as _adjacency
from . import index_lookup as _index_lookup
from .edge_records import get_edge
from .entities import key_to_string, node_key_to_bytes
from .node_records import get_node


def sample_nodes(store: KVStore, entity_serializer, guard: Callable, sample_size: int,
                 *, label: str | None = None,
                 node_filter: Callable | None = None, rng=None) -> list[str]:
    """Sample live node IDs using reservoir sampling.

    Memory is bounded by ``sample_size``; eligible keys are still scanned.
    """
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < 0
    ):
        raise ValueError("sample_size must be a non-negative integer")
    if node_filter is not None and not callable(node_filter):
        raise TypeError("node_filter must be callable")
    if sample_size == 0:
        return []
    rng = rng or random
    keys = (
        store.get_node_keys_generator()
        if label is None
        else _index_lookup.iter_node_ids_by_label(store, guard, label)
    )
    sample: list[str] = []
    seen = 0
    for key in keys:
        if node_filter is not None:
            node = get_node(store, entity_serializer, key)
            if node is None or not node_filter(node):
                continue
        seen += 1
        if len(sample) < sample_size:
            sample.append(key_to_string(key))
            continue
        replacement_idx = rng.randrange(seen)
        if replacement_idx < sample_size:
            sample[replacement_idx] = key_to_string(key)
    return sample


def sample_neighbors(store: KVStore, node_id, edge_type: str, direction: str = 'out',
                     sample_size: int = 10, rng=None, *, relation_hierarchy=None) -> list:
    """Sample typed neighbors using reservoir sampling."""
    rng = rng or random
    if relation_hierarchy is None:
        edge_types = (edge_type,)
    else:
        if not isinstance(relation_hierarchy, RelationHierarchy):
            raise ValueError("relation_hierarchy must be a RelationHierarchy")
        edge_types = relation_hierarchy.closure(edge_type)
    sample = []
    seen = 0
    for current_type in edge_types:
        for record in _adjacency.iter_typed_adjacency(
                store, node_key_to_bytes(node_id), current_type, direction):
            seen += 1
            if len(sample) < sample_size:
                sample.append(record)
                continue
            replacement_idx = rng.randrange(seen)
            if replacement_idx < sample_size:
                sample[replacement_idx] = record
    return sample


def sample_typed_paths(store: KVStore, seed_ids, pattern, rng=None, *,
                       relation_hierarchy=None) -> list:
    """Sample paths that follow an ordered typed edge pattern."""
    rng = rng or random
    if relation_hierarchy is not None and not isinstance(
        relation_hierarchy, RelationHierarchy
    ):
        raise ValueError("relation_hierarchy must be a RelationHierarchy")
    pattern = as_sampling_pattern(pattern)
    paths = []

    for seed_id in seed_ids:
        seed_id_bytes = node_key_to_bytes(seed_id)
        frontier = [{'seed': seed_id_bytes, 'path': [], 'current_node_id': seed_id_bytes}]
        for hop in pattern:
            next_frontier = []
            edge_type = hop.edge_type
            direction = hop.direction
            hop_sample_size = hop.sample_size
            for partial in frontier:
                sampled_records = sample_neighbors(
                    store,
                    partial['current_node_id'],
                    edge_type,
                    direction=direction,
                    sample_size=hop_sample_size,
                    rng=rng,
                    relation_hierarchy=relation_hierarchy,
                )
                for record in sampled_records:
                    next_frontier.append({
                        'seed': partial['seed'],
                        'path': partial['path'] + [record],
                        'current_node_id': record['neighbor_id'],
                    })
            frontier = next_frontier
            if not frontier:
                break
        for partial in frontier:
            paths.append({'seed': partial['seed'], 'path': partial['path']})
    return paths


def sample_typed_subgraph(store: KVStore, entity_serializer,
                          seed_ids, pattern, rng=None, *,
                          relation_hierarchy=None) -> dict:
    """Sample and materialize a typed subgraph around seed nodes."""
    paths = sample_typed_paths(
        store,
        seed_ids,
        pattern,
        rng=rng,
        relation_hierarchy=relation_hierarchy,
    )
    node_ids = {node_key_to_bytes(seed_id) for seed_id in seed_ids}
    edge_ids = set()
    for sampled_path in paths:
        node_ids.add(sampled_path['seed'])
        for hop in sampled_path['path']:
            node_ids.add(hop['source_id'])
            node_ids.add(hop['target_id'])
            edge_ids.add(hop['edge_id'])

    return {
        'nodes': {node_id: get_node(store, entity_serializer, node_id) for node_id in node_ids},
        'edges': {edge_id: get_edge(store, entity_serializer, edge_id) for edge_id in edge_ids},
        'paths': paths,
    }
