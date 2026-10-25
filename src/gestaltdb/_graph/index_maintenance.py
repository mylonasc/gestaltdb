"""Secondary-index maintenance writers (Phase 3 extraction).

These functions maintain label/type and configured property/range indexes
for node and edge writes. They are pure given an explicit backend store, the
configured index-property sets, and the record being written or removed; all
write orchestration (deciding *when* to call them) stays on ``GraphDB``.

``GraphDB._put/_delete_{node,edge}_indexes`` and
``_flush_index_rebuild_batches`` remain as thin private wrappers so existing
call sites in record writes, deletes, rebuilds, and columnar ingestion are
untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from .entities import (
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
)


def put_node_indexes(store: KVStore, indexed_node_properties, node) -> None:
    """Maintain label and configured property indexes for a node."""
    entries = []
    range_entries = []
    node_id = node.get_id_bytes
    for label in node.labels:
        entries.append(("node_label", [label.encode("utf-8")], node_id))
    for property_name in indexed_node_properties:
        if property_name in node.properties:
            raw_value = node.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            entries.append((
                "node_property",
                [property_name.encode("utf-8"), property_value],
                node_id,
            ))
            for label in node.labels:
                entries.append((
                    "node_label_property",
                    [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                    node_id,
                ))
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node_id))
                for label in node.labels:
                    range_entries.append(("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node_id))
    if entries:
        store.put_index_entries_bulk(entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)


def delete_node_indexes(store: KVStore, indexed_node_properties, node) -> None:
    """Remove label and configured property indexes for a node."""
    node_id = node.get_id_bytes
    for label in node.labels:
        store.delete_index_entry("node_label", [label.encode("utf-8")], node_id)
    for property_name in indexed_node_properties:
        if property_name in node.properties:
            raw_value = node.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            store.delete_index_entry(
                "node_property",
                [property_name.encode("utf-8"), property_value],
                node_id,
            )
            for label in node.labels:
                store.delete_index_entry(
                    "node_label_property",
                    [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                    node_id,
                )
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                store.delete_range_index_entry("node_property", [property_name.encode("utf-8")], range_value, node_id)
                for label in node.labels:
                    store.delete_range_index_entry("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node_id)


def flush_index_rebuild_batches(store: KVStore, entries: list, range_entries: list) -> None:
    """Write accumulated rebuild entries and clear the buffers."""
    if entries:
        store.put_index_entries_bulk(entries)
        entries.clear()
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)
        range_entries.clear()


def put_edge_indexes(store: KVStore, indexed_edge_properties, edge) -> None:
    """Maintain relationship type and configured property indexes."""
    entries = []
    range_entries = []
    edge_type = edge.get_type
    edge_id = edge.get_id_bytes
    if edge_type is not None:
        entries.append(("edge_type", [str(edge_type).encode("utf-8")], edge_id))
    for property_name in indexed_edge_properties:
        if property_name in edge.properties:
            raw_value = edge.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            entries.append((
                "edge_property",
                [property_name.encode("utf-8"), property_value],
                edge_id,
            ))
            if edge_type is not None:
                entries.append((
                    "edge_type_property",
                    [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                    edge_id,
                ))
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge_id))
                if edge_type is not None:
                    range_entries.append(("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge_id))
    if entries:
        store.put_index_entries_bulk(entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)


def delete_edge_indexes(store: KVStore, indexed_edge_properties, edge) -> None:
    """Remove relationship type and configured property indexes."""
    edge_type = edge.get_type
    edge_id = edge.get_id_bytes
    if edge_type is not None:
        store.delete_index_entry("edge_type", [str(edge_type).encode("utf-8")], edge_id)
    for property_name in indexed_edge_properties:
        if property_name in edge.properties:
            raw_value = edge.properties[property_name]
            property_value = _property_value_to_index_bytes(raw_value)
            store.delete_index_entry(
                "edge_property",
                [property_name.encode("utf-8"), property_value],
                edge_id,
            )
            if edge_type is not None:
                store.delete_index_entry(
                    "edge_type_property",
                    [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                    edge_id,
                )
            range_value = _property_value_to_range_index_bytes(raw_value)
            if range_value is not None:
                store.delete_range_index_entry("edge_property", [property_name.encode("utf-8")], range_value, edge_id)
                if edge_type is not None:
                    store.delete_range_index_entry("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge_id)
