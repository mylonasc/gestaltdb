"""Secondary-index rebuild scans (Phase 3 extraction).

``rebuild_node_indexes``/``rebuild_edge_indexes`` rescan all records and
re-emit secondary index entries in batches. The transaction boundary stays
on ``GraphDB``: wrappers recurse into a transaction when required and
delegate the store-bound scan here. Stale markers are cleared through
``index_maintenance`` with an explicit metadata key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from . import index_maintenance as _index_maintenance
from .edge_records import get_edge
from .entities import (
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
)
from .node_records import get_node


def rebuild_node_indexes(store: KVStore, entity_serializer, indexed_node_properties,
                         stale_key: bytes, *, labels: bool = True,
                         properties=None, batch_size: int = 100_000) -> dict[str, int]:
    """Rebuild requested node secondary indexes in one node scan."""
    property_names = sorted(indexed_node_properties if properties is None else set(properties))
    entries = []
    range_entries = []
    rebuilt = {"node_label": 0}
    rebuilt.update({f"node_property:{property_name}": 0 for property_name in property_names})

    for node_id in store.get_node_keys_generator():
        node = get_node(store, entity_serializer, node_id)
        if node is None:
            continue
        node_key = node.get_id_bytes
        if labels:
            for label in node.labels:
                entries.append(("node_label", [label.encode("utf-8")], node_key))
                rebuilt["node_label"] += 1
        for property_name in property_names:
            if property_name not in node.properties:
                continue
            raw_value = node.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            entries.append(("node_property", [property_name.encode("utf-8"), property_value], node_key))
            for label in node.labels:
                entries.append((
                    "node_label_property",
                    [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                    node_key,
                ))
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node_key))
                for label in node.labels:
                    range_entries.append((
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8")],
                        range_value,
                        node_key,
                    ))
            rebuilt[f"node_property:{property_name}"] += 1
        if len(entries) + len(range_entries) >= batch_size:
            _index_maintenance.flush_index_rebuild_batches(store, entries, range_entries)

    _index_maintenance.flush_index_rebuild_batches(store, entries, range_entries)
    if labels:
        _index_maintenance.clear_stale_indexes(store, stale_key, "node_label")
    if property_names and set(indexed_node_properties).issubset(set(property_names)):
        _index_maintenance.clear_stale_indexes(store, stale_key, "node_property")
    rebuilt["node_property"] = sum(rebuilt[f"node_property:{property_name}"] for property_name in property_names)
    return rebuilt


def rebuild_edge_indexes(store: KVStore, entity_serializer, indexed_edge_properties,
                         stale_key: bytes, *, edge_types: bool = True,
                         properties=None, batch_size: int = 100_000) -> dict[str, int]:
    """Rebuild requested edge secondary indexes in one edge scan."""
    property_names = sorted(indexed_edge_properties if properties is None else set(properties))
    entries = []
    range_entries = []
    rebuilt = {"edge_type": 0}
    rebuilt.update({f"edge_property:{property_name}": 0 for property_name in property_names})

    for edge_id in store.get_edge_keys_generator():
        edge = get_edge(store, entity_serializer, edge_id)
        if edge is None:
            continue
        edge_key = edge.get_id_bytes
        edge_type = edge.get_type
        if edge_types and edge_type is not None:
            entries.append(("edge_type", [str(edge_type).encode("utf-8")], edge_key))
            rebuilt["edge_type"] += 1
        for property_name in property_names:
            if property_name not in edge.properties:
                continue
            raw_value = edge.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            entries.append(("edge_property", [property_name.encode("utf-8"), property_value], edge_key))
            if edge_type is not None:
                entries.append((
                    "edge_type_property",
                    [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                    edge_key,
                ))
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge_key))
                if edge_type is not None:
                    range_entries.append((
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
                        range_value,
                        edge_key,
                    ))
            rebuilt[f"edge_property:{property_name}"] += 1
        if len(entries) + len(range_entries) >= batch_size:
            _index_maintenance.flush_index_rebuild_batches(store, entries, range_entries)

    _index_maintenance.flush_index_rebuild_batches(store, entries, range_entries)
    if edge_types:
        _index_maintenance.clear_stale_indexes(store, stale_key, "edge_type")
    if property_names and set(indexed_edge_properties).issubset(set(property_names)):
        _index_maintenance.clear_stale_indexes(store, stale_key, "edge_property")
    rebuilt["edge_property"] = sum(rebuilt[f"edge_property:{property_name}"] for property_name in property_names)
    return rebuilt
