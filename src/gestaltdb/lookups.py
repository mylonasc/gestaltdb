"""Index-backed decoded entity reads over one current-graph scope.

``EntityLookup`` composes ``IndexReader`` with current node/edge readers so
callers can fetch decoded entities without a GraphDB. It owns no writes, index
DDL, or temporal history; snapshot views can expose it read-only.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from ._graph.entities import Edge, Node

if TYPE_CHECKING:
    from .current import StorageSession

from .current import EdgeReader, NodeReader
from .indexes import IndexReader


class EntityLookup:
    """Decoded node/edge reads backed by configured current indexes."""

    def __init__(self, session: StorageSession) -> None:
        self._session = session
        self._indexes = IndexReader(session)
        self._nodes = NodeReader(session)
        self._edges = EdgeReader(session)

    def nodes_by_label(self, label: str) -> list[Node | None]:
        """Return decoded nodes with a label using the label index."""
        return [self._nodes.get(key) for key in self._indexes.iter_node_ids_by_label(label)]

    def count_nodes_by_label(self, label: str) -> int:
        """Return the number of indexed nodes for a label."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label(label))

    def nodes_by_property(self, property_name: str, value: object) -> list[Node | None]:
        """Return decoded nodes from an exact-match property index.

        The property must have an index (see ``create_node_property_index``);
        unindexed properties return ``[]`` rather than raising.
        """
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_property(property_name, value)]

    def count_nodes_by_property(self, property_name: str, value: object) -> int:
        """Return the number of indexed nodes for an exact property value."""
        return sum(1 for _ in
                   self._indexes.iter_node_ids_by_property(property_name, value))

    def nodes_by_label_property(self, label: str, property_name: str, value: object) -> list[Node | None]:
        """Return decoded nodes from the composite label/property index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_label_property(
                    label, property_name, value)]

    def count_nodes_by_label_property(self, label: str, property_name: str, value: object) -> int:
        """Return the number of nodes indexed for a label and property value."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label_property(
            label, property_name, value))

    def nodes_by_property_range(self, property_name: str, start_value: object = None,
                                end_value: object = None, include_start: bool = True,
                                include_end: bool = True) -> list[Node | None]:
        """Return decoded nodes from a scalar property range index.

        The property must have an index; unindexed properties return ``[]``.
        """
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_property_range(
                    property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_property_range(self, property_name: str, start_value: object = None,
                                      end_value: object = None, include_start: bool = True,
                                      include_end: bool = True) -> int:
        """Return the number of nodes indexed in a scalar property range."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_property_range(
            property_name, start_value, end_value, include_start, include_end))

    def nodes_by_label_property_range(self, label: str, property_name: str,
                                      start_value: object = None, end_value: object = None,
                                      include_start: bool = True,
                                      include_end: bool = True) -> list[Node | None]:
        """Return decoded nodes from a composite label/property range index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_label_property_range(
                    label, property_name, start_value, end_value,
                    include_start, include_end)]

    def count_nodes_by_label_property_range(self, label: str, property_name: str,
                                            start_value: object = None, end_value: object = None,
                                            include_start: bool = True,
                                            include_end: bool = True) -> int:
        """Return the number of nodes indexed for a label/property range."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label_property_range(
            label, property_name, start_value, end_value, include_start, include_end))

    def range_query_nodes(self, property_name: str, start_val: object, end_val: object) -> Iterator[Node | None]:
        """Yield decoded nodes through the store's raw range iteration.

        .. deprecated::
            Prefer ``nodes_by_property_range`` with a configured property
            index. This keeps the legacy exact raw key format and can return
            ``[]`` where the indexed API returns rows.
        """
        start_key = f"IDX:N:{property_name}:{start_val}:".encode("utf-8")
        end_key = f"IDX:N:{property_name}:{end_val}:\xff".encode("utf-8")
        for key, _value in self._session.store.range_iter(start_key, end_key):
            # The stub keeps its legacy exact raw key format: the trailing
            # segment is forwarded undecoded, exactly as historical callers
            # (and their monkeypatched stores) expect.
            yield self._nodes.get(cast(bytes, key.decode("utf-8").split(":")[-1]))

    def edges_by_type(self, edge_type: str) -> list[Edge | None]:
        """Return decoded edges from the relationship type catalog."""
        return [self._edges.get(key) for key in self._indexes.iter_edge_ids_by_type(edge_type)]

    def count_edges_by_type(self, edge_type: str) -> int:
        """Return the number of indexed edges for a relationship type."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type(edge_type))

    def edges_by_property(self, property_name: str, value: object) -> list[Edge | None]:
        """Return decoded edges from an exact-match property index.

        The property must have an index; unindexed properties return ``[]``.
        """
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_property(property_name, value)]

    def count_edges_by_property(self, property_name: str, value: object) -> int:
        """Return the number of indexed edges for an exact property value."""
        return sum(1 for _ in
                   self._indexes.iter_edge_ids_by_property(property_name, value))

    def edges_by_type_property(self, edge_type: str, property_name: str, value: object) -> list[Edge | None]:
        """Return decoded edges from the composite type/property index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_type_property(
                    edge_type, property_name, value)]

    def count_edges_by_type_property(self, edge_type: str, property_name: str, value: object) -> int:
        """Return the number of edges indexed for a type and property value."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type_property(
            edge_type, property_name, value))

    def edges_by_property_range(self, property_name: str, start_value: object = None,
                                end_value: object = None, include_start: bool = True,
                                include_end: bool = True) -> list[Edge | None]:
        """Return decoded edges from a scalar property range index.

        The property must have an index; unindexed properties return ``[]``.
        """
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_property_range(
                    property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_property_range(self, property_name: str, start_value: object = None,
                                      end_value: object = None, include_start: bool = True,
                                      include_end: bool = True) -> int:
        """Return the number of edges indexed in a scalar property range."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_property_range(
            property_name, start_value, end_value, include_start, include_end))

    def edges_by_type_property_range(self, edge_type: str, property_name: str,
                                     start_value: object = None, end_value: object = None,
                                     include_start: bool = True,
                                     include_end: bool = True) -> list[Edge | None]:
        """Return decoded edges from a composite type/property range index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_type_property_range(
                    edge_type, property_name, start_value, end_value,
                    include_start, include_end)]

    def count_edges_by_type_property_range(self, edge_type: str, property_name: str,
                                           start_value: object = None, end_value: object = None,
                                           include_start: bool = True,
                                           include_end: bool = True) -> int:
        """Return the number of edges indexed for a type/property range."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type_property_range(
            edge_type, property_name, start_value, end_value, include_start, include_end))
