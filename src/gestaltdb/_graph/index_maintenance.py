"""Secondary-index maintenance writers (Phase 3 extraction).

These functions maintain label/type and configured property/range indexes
for node and edge writes. They are pure given an explicit backend store, the
configured index-property sets, and the record being written or removed; write
orchestration lives in the scope-owned current repositories and services
(``NodeRepository``/``EdgeRepository``/``IndexService``/columnar loaders).
"""

from __future__ import annotations

import json

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


def load_stale_indexes(store: KVStore, key: bytes) -> set[str]:

    """Load index families known to be stale after deferred bulk ingestion."""
    try:
        payload = store.get_metadata(key)
    except NotImplementedError:
        return set()
    if not payload:
        return set()
    return set(json.loads(payload.decode("utf-8")))


def persist_stale_indexes(store: KVStore, key: bytes, stale_indexes: set[str]) -> None:
    payload = json.dumps(sorted(stale_indexes), separators=(",", ":")).encode("utf-8")
    store.put_metadata(key, payload)


def mark_indexes_stale(store: KVStore, key: bytes, *index_names: str) -> None:
    """Mark index families stale after a deferred bulk load."""
    if not index_names:
        return
    try:
        stale_indexes = load_stale_indexes(store, key)
        stale_indexes.update(index_names)
        persist_stale_indexes(store, key, stale_indexes)
    except NotImplementedError:
        return


def clear_stale_indexes(store: KVStore, key: bytes, *index_names: str) -> None:
    """Clear stale markers after rebuilding index families."""
    try:
        stale_indexes = load_stale_indexes(store, key)
        stale_indexes.difference_update(index_names)
        persist_stale_indexes(store, key, stale_indexes)
    except NotImplementedError:
        return


def ensure_indexes_current(store: KVStore, key: bytes, *index_names: str) -> None:
    """Prevent stale secondary indexes from silently returning wrong results."""
    stale = load_stale_indexes(store, key).intersection(index_names)
    if stale:
        names = ", ".join(sorted(stale))
        raise RuntimeError(f"stale indexes require rebuild before query: {names}")


def load_property_index_metadata(store: KVStore, key: bytes) -> set[str]:
    """Load persisted property index definitions from backend metadata."""
    try:
        payload = store.get_metadata(key)
    except NotImplementedError:
        return set()
    if not payload:
        return set()
    return set(json.loads(payload.decode("utf-8")))


def persist_property_index_metadata(store: KVStore, key: bytes, property_names: set[str]) -> None:
    """Persist property index definitions to backend metadata."""
    payload = json.dumps(sorted(property_names), separators=(",", ":")).encode("utf-8")
    store.put_metadata(key, payload)


def load_node_constraint_metadata(store: KVStore, key: bytes) -> list[dict[str, str]]:
    """Load persisted node constraint definitions from backend metadata."""
    try:
        payload = store.get_metadata(key)
    except NotImplementedError:
        return []
    if not payload:
        return []
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict) or decoded.get("version") != 1:
        raise ValueError("Unsupported node constraint catalog format")
    constraints = decoded.get("constraints", [])
    if not isinstance(constraints, list):
        raise ValueError("Invalid node constraint catalog")
    return [dict(constraint) for constraint in constraints]
