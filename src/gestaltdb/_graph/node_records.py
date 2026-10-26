"""Node record orchestration (Phase 3 extraction, records side).

``put_node``/``delete_node`` coordinate canonical node records, legacy
adjacency blobs, and secondary indexes; ``delete_node`` cascades through
edge deletion. The transaction boundary stays on ``GraphDB``: wrappers open
a transaction when required and delegate the store-bound core here.

``update_node`` stays on ``GraphDB``: it composes the public
get/merge/put API and carries no storage logic of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from . import edge_records as _edge_records
from . import index_maintenance as _index_maintenance
from .entities import (
    Node,
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
    node_key_to_bytes,
)


def get_node(store: KVStore, entity_serializer, node_id):
    """Return a node by byte key, or ``None`` when absent."""
    data = store.get_node(node_id)
    if data:
        return entity_serializer.deserialize(data, 'Node')
    return None


def get_nodes(store: KVStore, raw_serializer, node_ids: list) -> list:
    """Return decoded nodes in request order (``None`` for missing IDs)."""
    results = []
    raw_dict = store.get_nodes_bulk(node_ids)  # dict[node_id, bytes]
    for node_id in node_ids:
        raw_data = raw_dict.get(node_id)
        if raw_data is not None:
            node_dict = raw_serializer.deserialize(raw_data)
            results.append(Node.from_dict(node_dict))
        else:
            results.append(None)  # or skip it
    return results


def get_node_keys_generator(store: KVStore, num_nodes=None, key_offset=None):
    """Yield node keys from the backing store."""
    return store.get_node_keys_generator(num_nodes, key_offset)


def put_node(store: KVStore, entity_serializer, indexed_node_properties, node: Node) -> None:
    """Store a node and maintain label and property indexes."""
    old_node = get_node(store, entity_serializer, node.get_id_bytes)
    if old_node is not None:
        _index_maintenance.delete_node_indexes(store, indexed_node_properties, old_node)
    value = entity_serializer.serialize(node, 'Node')
    store.put_node(node.get_id_bytes, value)
    _index_maintenance.put_node_indexes(store, indexed_node_properties, node)


def delete_node(store: KVStore, entity_serializer, raw_serializer,
                indexed_node_properties, indexed_edge_properties,
                adj_cache: dict, node_id) -> None:
    """Delete a node, its incident edges, adjacency, and indexes."""
    node_id = node_key_to_bytes(node_id)
    incident_edge_ids = []
    for edge_id in store.get_edge_keys_generator():
        edge = _edge_records.get_edge(store, entity_serializer, edge_id)
        if edge is None:
            continue
        if node_key_to_bytes(edge.source) == node_id or node_key_to_bytes(edge.target) == node_id:
            incident_edge_ids.append(edge_id)

    for edge_id in incident_edge_ids:
        _edge_records.delete_edge(store, entity_serializer, raw_serializer,
                                  indexed_edge_properties, adj_cache, edge_id)

    old_node = get_node(store, entity_serializer, node_id)
    if old_node is not None:
        _index_maintenance.delete_node_indexes(store, indexed_node_properties, old_node)
    store.delete_adjacency(node_id)
    store.delete_node(node_id)


def put_nodes(store, entity_serializer, indexed_node_properties, nodes: list) -> None:
    """Store multiple nodes and maintain label/property indexes in bulk."""
    to_store = {}
    index_entries = []
    range_entries = []
    for n in nodes:
        old_node = get_node(store, entity_serializer, n.get_id_bytes)
        if old_node is not None:
            _index_maintenance.delete_node_indexes(store, indexed_node_properties, old_node)
        to_store[n.get_id_bytes] = entity_serializer.serialize(n, "Node")
        for label in n.labels:
            index_entries.append(("node_label", [label.encode("utf-8")], n.get_id_bytes))
        for property_name in indexed_node_properties:
            if property_name in n.properties:
                property_value = _property_value_to_index_bytes(n.properties[property_name])
                index_entries.append((
                    "node_property",
                    [property_name.encode("utf-8"), property_value],
                    n.get_id_bytes,
                ))
                for label in n.labels:
                    index_entries.append((
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                        n.get_id_bytes,
                    ))
                range_value = _property_value_to_range_index_bytes(n.properties[property_name])
                if range_value is not None:
                    range_entries.append((
                        "node_property",
                        [property_name.encode("utf-8")],
                        range_value,
                        n.get_id_bytes,
                    ))
                    for label in n.labels:
                        range_entries.append((
                            "node_label_property",
                            [label.encode("utf-8"), property_name.encode("utf-8")],
                            range_value,
                            n.get_id_bytes,
                        ))
    store.put_nodes_bulk(to_store)
    if index_entries:
        store.put_index_entries_bulk(index_entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)
