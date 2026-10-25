"""Secondary-index ID lookups (Phase 3 extraction, read side).

These functions implement ``IndexReader``'s ``iter_*`` lookups against an
explicit backend store. The ``ensure_current`` argument is the reader's
freshness guard; it runs at the same
point as before — deferred to first iteration for generator lookups,
eagerly for range lookups — so stale-index failures keep their exact
timing. IndexReader owns the guard without GraphDB callbacks.

Entity-mapping wrappers (``nodes_by_*``, ``edges_by_*``, ``count_*``) stay
on ``GraphDB``; they compose these iterators with ``get_node/get_edge``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from .entities import (
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
)


def iter_node_ids_by_label(store: KVStore, ensure_current: Callable[..., None], label: str):
    """Yield node IDs from the label index."""
    ensure_current("node_label")
    yield from store.iter_index_prefix("node_label", [label.encode("utf-8")])


def iter_node_ids_by_property(store: KVStore, ensure_current: Callable[..., None],
                              property_name: str, value):
    """Yield node IDs from an exact-match property index."""
    ensure_current("node_property")
    yield from store.iter_index_prefix(
        "node_property",
        [property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
    )


def iter_node_ids_by_label_property(store: KVStore, ensure_current: Callable[..., None],
                                    label: str, property_name: str, value):
    """Yield node IDs from the composite label/property exact-match index."""
    ensure_current("node_label", "node_property")
    yield from store.iter_index_prefix(
        "node_label_property",
        [label.encode("utf-8"), property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
    )


def iter_node_ids_by_property_range(store: KVStore, ensure_current: Callable[..., None],
                                    property_name: str, start_value=None, end_value=None,
                                    include_start: bool = True, include_end: bool = True):
    """Yield node IDs from a scalar property range index."""
    ensure_current("node_property")
    start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
    end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
    if (start_value is not None and start is None) or (end_value is not None and end is None):
        return iter(())
    if start is not None and end is not None and start[:1] != end[:1]:
        return iter(())
    return store.iter_range_index(
        "node_property",
        [property_name.encode("utf-8")],
        start,
        end,
        include_start,
        include_end,
    )


def iter_node_ids_by_label_property_range(store: KVStore, ensure_current: Callable[..., None],
                                           label: str, property_name: str, start_value=None,
                                           end_value=None, include_start: bool = True,
                                           include_end: bool = True):
    """Yield node IDs from a composite label/property range index."""
    ensure_current("node_label", "node_property")
    start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
    end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
    if (start_value is not None and start is None) or (end_value is not None and end is None):
        return iter(())
    if start is not None and end is not None and start[:1] != end[:1]:
        return iter(())
    return store.iter_range_index(
        "node_label_property",
        [label.encode("utf-8"), property_name.encode("utf-8")],
        start,
        end,
        include_start,
        include_end,
    )


def iter_edge_ids_by_type(store: KVStore, ensure_current: Callable[..., None], edge_type: str):
    """Yield edge IDs from the relationship type catalog."""
    ensure_current("edge_type")
    yield from store.iter_index_prefix("edge_type", [str(edge_type).encode("utf-8")])


def iter_edge_ids(store: KVStore, num_edges=None, key_offset=None):
    """Yield all canonical edge IDs from the backing store."""
    if num_edges == 0:
        return
    yield from store.get_edge_keys_generator(num_edges, key_offset)


def iter_edge_ids_by_property(store: KVStore, ensure_current: Callable[..., None],
                              property_name: str, value):
    """Yield edge IDs from an exact-match edge property index."""
    ensure_current("edge_property")
    yield from store.iter_index_prefix(
        "edge_property",
        [property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
    )


def iter_edge_ids_by_type_property(store: KVStore, ensure_current: Callable[..., None],
                                   edge_type: str, property_name: str, value):
    """Yield edge IDs from the composite type/property exact-match index."""
    ensure_current("edge_type", "edge_property")
    yield from store.iter_index_prefix(
        "edge_type_property",
        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
    )


def iter_edge_ids_by_property_range(store: KVStore, ensure_current: Callable[..., None],
                                    property_name: str, start_value=None, end_value=None,
                                    include_start: bool = True, include_end: bool = True):
    """Yield edge IDs from a scalar property range index."""
    ensure_current("edge_property")
    start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
    end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
    if (start_value is not None and start is None) or (end_value is not None and end is None):
        return iter(())
    if start is not None and end is not None and start[:1] != end[:1]:
        return iter(())
    return store.iter_range_index(
        "edge_property",
        [property_name.encode("utf-8")],
        start,
        end,
        include_start,
        include_end,
    )


def iter_edge_ids_by_type_property_range(store: KVStore, ensure_current: Callable[..., None],
                                         edge_type: str, property_name: str, start_value=None,
                                         end_value=None, include_start: bool = True,
                                         include_end: bool = True):
    """Yield edge IDs from a composite type/property range index."""
    ensure_current("edge_type", "edge_property")
    start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
    end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
    if (start_value is not None and start is None) or (end_value is not None and end is None):
        return iter(())
    if start is not None and end is not None and start[:1] != end[:1]:
        return iter(())
    return store.iter_range_index(
        "edge_type_property",
        [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
        start,
        end,
        include_start,
        include_end,
    )
