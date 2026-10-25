"""Scope-bound current-graph secondary indexes, separate from temporal history.

IndexReader grants lookup authority; IndexService adds explicit DDL and rebuilds.
Both borrow a StorageSession. Neither needs a GraphDB or owns backend lifetime.
"""

from __future__ import annotations

from ._graph import index_lookup as lookup
from ._graph import index_maintenance as maintenance
from ._graph import index_rebuild as rebuild

STALE_KEY = b"schema:indexes:stale"
NODE_PROPERTIES_KEY = b"schema:indexes:node_properties"
EDGE_PROPERTIES_KEY = b"schema:indexes:edge_properties"


class IndexReader:
    """Read current secondary indexes without DDL or publication authority."""

    def __init__(self, session):
        self._session = session

    def ensure_current(self, *families):
        """Fail closed on deferred current-graph index families."""
        maintenance.ensure_indexes_current(self._session.store, STALE_KEY, *families)

    def iter_node_ids_by_label(self, label):
        return lookup.iter_node_ids_by_label(self._session.store, self.ensure_current, label)

    def iter_node_ids_by_property(self, property_name, value):
        return lookup.iter_node_ids_by_property(
            self._session.store, self.ensure_current, property_name, value)

    def iter_node_ids_by_label_property(self, label, property_name, value):
        return lookup.iter_node_ids_by_label_property(
            self._session.store, self.ensure_current, label, property_name, value)

    def iter_node_ids_by_property_range(self, property_name, start_value=None,
                                       end_value=None, include_start=True, include_end=True):
        return lookup.iter_node_ids_by_property_range(
            self._session.store, self.ensure_current, property_name,
            start_value, end_value, include_start, include_end)

    def iter_node_ids_by_label_property_range(self, label, property_name, start_value=None,
                                             end_value=None, include_start=True, include_end=True):
        return lookup.iter_node_ids_by_label_property_range(
            self._session.store, self.ensure_current, label, property_name,
            start_value, end_value, include_start, include_end)

    def iter_edge_ids_by_type(self, edge_type):
        return lookup.iter_edge_ids_by_type(self._session.store, self.ensure_current, edge_type)

    def iter_edge_ids_by_property(self, property_name, value):
        return lookup.iter_edge_ids_by_property(
            self._session.store, self.ensure_current, property_name, value)

    def iter_edge_ids_by_type_property(self, edge_type, property_name, value):
        return lookup.iter_edge_ids_by_type_property(
            self._session.store, self.ensure_current, edge_type, property_name, value)

    def iter_edge_ids_by_property_range(self, property_name, start_value=None,
                                       end_value=None, include_start=True, include_end=True):
        return lookup.iter_edge_ids_by_property_range(
            self._session.store, self.ensure_current, property_name,
            start_value, end_value, include_start, include_end)

    def iter_edge_ids_by_type_property_range(self, edge_type, property_name, start_value=None,
                                            end_value=None, include_start=True, include_end=True):
        return lookup.iter_edge_ids_by_type_property_range(
            self._session.store, self.ensure_current, edge_type, property_name,
            start_value, end_value, include_start, include_end)

    def statistics(self):
        """Return the configured property indexes for this exact scope."""
        return {
            "indexed_node_properties": tuple(sorted(self._session.indexed_node_properties)),
            "indexed_edge_properties": tuple(sorted(self._session.indexed_edge_properties)),
        }


class IndexService(IndexReader):
    """Current index DDL/rebuilds with explicit post-commit config publication."""

    @property
    def reader(self):
        return IndexReader(self._session)

    def rebuild_node_indexes(self, *, labels=True, properties=None, batch_size=100_000):
        with self._session.write_scope() as session:
            return rebuild.rebuild_node_indexes(
                session.store, session.entity_serializer, session.indexed_node_properties,
                STALE_KEY, labels=labels, properties=properties, batch_size=batch_size)

    def rebuild_edge_indexes(self, *, edge_types=True, properties=None, batch_size=100_000):
        with self._session.write_scope() as session:
            return rebuild.rebuild_edge_indexes(
                session.store, session.entity_serializer, session.indexed_edge_properties,
                STALE_KEY, edge_types=edge_types, properties=properties, batch_size=batch_size)

    def _create_property_index(self, kind, property_name):
        attribute = f"indexed_{kind}_properties"
        key = NODE_PROPERTIES_KEY if kind == "node" else EDGE_PROPERTIES_KEY
        # Always stage a new set. A failed scan must not mutate the live
        # configuration, even on backends without atomic transactions.
        with self._session.write_scope() as session:
            names = set(getattr(session, attribute))
            names.add(property_name)
            if kind == "node":
                result = rebuild.rebuild_node_indexes(
                    session.store, session.entity_serializer, names, STALE_KEY,
                    labels=False, properties={property_name})
            else:
                result = rebuild.rebuild_edge_indexes(
                    session.store, session.entity_serializer, names, STALE_KEY,
                    edge_types=False, properties={property_name})
            maintenance.persist_property_index_metadata(session.store, key, names)
        getattr(self._session, attribute).add(property_name)
        return result[f"{kind}_property:{property_name}"]

    def create_node_property_index(self, property_name):
        """Register an index after its rebuild and metadata commit succeed."""
        return self._create_property_index("node", property_name)

    def create_edge_property_index(self, property_name):
        """Register an index after its rebuild and metadata commit succeed."""
        return self._create_property_index("edge", property_name)

    def rebuild_node_property_index(self, property_name):
        return self.rebuild_node_indexes(
            labels=False, properties={property_name})[f"node_property:{property_name}"]

    def rebuild_edge_property_index(self, property_name):
        return self.rebuild_edge_indexes(
            edge_types=False, properties={property_name})[f"edge_property:{property_name}"]

    def rebuild_label_index(self):
        return self.rebuild_node_indexes(labels=True, properties=set())["node_label"]

    def rebuild_relationship_type_index(self):
        return self.rebuild_edge_indexes(edge_types=True, properties=set())["edge_type"]
