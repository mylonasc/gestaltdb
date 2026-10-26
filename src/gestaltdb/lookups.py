"""Index-backed decoded entity reads over one current-graph scope.

``EntityLookup`` composes ``IndexReader`` with current node/edge readers so
callers can fetch decoded entities without a GraphDB. It owns no writes, index
DDL, or temporal history; snapshot views can expose it read-only.
"""

from __future__ import annotations

from .current import EdgeReader, NodeReader
from .indexes import IndexReader


class EntityLookup:
    """Decoded node/edge reads backed by configured current indexes."""

    def __init__(self, session):
        self._session = session
        self._indexes = IndexReader(session)
        self._nodes = NodeReader(session)
        self._edges = EdgeReader(session)

    def nodes_by_label(self, label: str):
        """Return decoded nodes with a label using the label index."""
        return [self._nodes.get(key) for key in self._indexes.iter_node_ids_by_label(label)]

    def count_nodes_by_label(self, label: str) -> int:
        """Return the number of indexed nodes for a label."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label(label))

    def nodes_by_property(self, property_name: str, value):
        """Return decoded nodes from an exact-match property index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_property(property_name, value)]

    def count_nodes_by_property(self, property_name: str, value) -> int:
        """Return the number of indexed nodes for an exact property value."""
        return sum(1 for _ in
                   self._indexes.iter_node_ids_by_property(property_name, value))

    def nodes_by_label_property(self, label: str, property_name: str, value):
        """Return decoded nodes from the composite label/property index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_label_property(
                    label, property_name, value)]

    def count_nodes_by_label_property(self, label: str, property_name: str, value) -> int:
        """Return the number of nodes indexed for a label and property value."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label_property(
            label, property_name, value))

    def nodes_by_property_range(self, property_name: str, start_value=None,
                                end_value=None, include_start: bool = True,
                                include_end: bool = True):
        """Return decoded nodes from a scalar property range index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_property_range(
                    property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_property_range(self, property_name: str, start_value=None,
                                      end_value=None, include_start: bool = True,
                                      include_end: bool = True) -> int:
        """Return the number of nodes indexed in a scalar property range."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_property_range(
            property_name, start_value, end_value, include_start, include_end))

    def nodes_by_label_property_range(self, label: str, property_name: str,
                                      start_value=None, end_value=None,
                                      include_start: bool = True,
                                      include_end: bool = True):
        """Return decoded nodes from a composite label/property range index."""
        return [self._nodes.get(key) for key in
                self._indexes.iter_node_ids_by_label_property_range(
                    label, property_name, start_value, end_value,
                    include_start, include_end)]

    def count_nodes_by_label_property_range(self, label: str, property_name: str,
                                            start_value=None, end_value=None,
                                            include_start: bool = True,
                                            include_end: bool = True) -> int:
        """Return the number of nodes indexed for a label/property range."""
        return sum(1 for _ in self._indexes.iter_node_ids_by_label_property_range(
            label, property_name, start_value, end_value, include_start, include_end))

    def range_query_nodes(self, property_name: str, start_val, end_val):
        """Yield decoded nodes through the store's raw range iteration."""
        start_key = f"IDX:N:{property_name}:{start_val}:".encode("utf-8")
        end_key = f"IDX:N:{property_name}:{end_val}:\xff".encode("utf-8")
        for key, _value in self._session.store.range_iter(start_key, end_key):
            yield self._nodes.get(key.decode("utf-8").split(":")[-1])

    def edges_by_type(self, edge_type: str):
        """Return decoded edges from the relationship type catalog."""
        return [self._edges.get(key) for key in self._indexes.iter_edge_ids_by_type(edge_type)]

    def count_edges_by_type(self, edge_type: str) -> int:
        """Return the number of indexed edges for a relationship type."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type(edge_type))

    def edges_by_property(self, property_name: str, value):
        """Return decoded edges from an exact-match property index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_property(property_name, value)]

    def count_edges_by_property(self, property_name: str, value) -> int:
        """Return the number of indexed edges for an exact property value."""
        return sum(1 for _ in
                   self._indexes.iter_edge_ids_by_property(property_name, value))

    def edges_by_type_property(self, edge_type: str, property_name: str, value):
        """Return decoded edges from the composite type/property index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_type_property(
                    edge_type, property_name, value)]

    def count_edges_by_type_property(self, edge_type: str, property_name: str, value) -> int:
        """Return the number of edges indexed for a type and property value."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type_property(
            edge_type, property_name, value))

    def edges_by_property_range(self, property_name: str, start_value=None,
                                end_value=None, include_start: bool = True,
                                include_end: bool = True):
        """Return decoded edges from a scalar property range index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_property_range(
                    property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_property_range(self, property_name: str, start_value=None,
                                      end_value=None, include_start: bool = True,
                                      include_end: bool = True) -> int:
        """Return the number of edges indexed in a scalar property range."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_property_range(
            property_name, start_value, end_value, include_start, include_end))

    def edges_by_type_property_range(self, edge_type: str, property_name: str,
                                     start_value=None, end_value=None,
                                     include_start: bool = True,
                                     include_end: bool = True):
        """Return decoded edges from a composite type/property range index."""
        return [self._edges.get(key) for key in
                self._indexes.iter_edge_ids_by_type_property_range(
                    edge_type, property_name, start_value, end_value,
                    include_start, include_end)]

    def count_edges_by_type_property_range(self, edge_type: str, property_name: str,
                                           start_value=None, end_value=None,
                                           include_start: bool = True,
                                           include_end: bool = True) -> int:
        """Return the number of edges indexed for a type/property range."""
        return sum(1 for _ in self._indexes.iter_edge_ids_by_type_property_range(
            edge_type, property_name, start_value, end_value, include_start, include_end))
