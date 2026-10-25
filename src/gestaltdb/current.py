"""Store-bound current-graph sessions and node/edge repositories.

These operations concern mutable graph records, not temporal claims/history.
Use ``graph.nodes``/``graph.edges`` or construct repositories over StorageSession.
See ``docs/architecture.rst`` for a complete example and lifetime boundaries.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading

from ._graph import edge_records, ingest_edges, node_records
from ._graph.entities import Edge, GraphEntityDictSerializer, Node
from ._graph.index_maintenance import load_property_index_metadata
from .concurrency import DEFAULT_LOCK_MANAGER


class StorageSession:
    """Borrow one store scope and own ordinary current-write coordination.

    Codecs and index definitions are explicit; transaction children copy index
    configuration and start with a fresh adjacency cache. The session neither
    closes the caller's store nor initializes identity or temporal history.
    ``write_guard`` allows the owning database to supply its established writer
    coordination; standalone sessions use the same durable writer-lock file.
    """

    def __init__(self, store, serializer, *, indexed_node_properties=None,
                 indexed_edge_properties=None, adjacency_counts=None,
                 entity_serializer=None, transaction_bound=False,
                 write_guard=None, lock_manager=None):
        self.store = store
        self.serializer = serializer
        self.entity_serializer = (GraphEntityDictSerializer(serializer)
                                  if entity_serializer is None else entity_serializer)
        self.indexed_node_properties = (
            load_property_index_metadata(store, b"schema:indexes:node_properties")
            if indexed_node_properties is None else indexed_node_properties)
        self.indexed_edge_properties = (
            load_property_index_metadata(store, b"schema:indexes:edge_properties")
            if indexed_edge_properties is None else indexed_edge_properties)
        self.adjacency_counts = {} if adjacency_counts is None else adjacency_counts
        self.transaction_bound = transaction_bound
        self.lock_manager = DEFAULT_LOCK_MANAGER if lock_manager is None else lock_manager
        self._memory_lock = threading.RLock()
        self._write_guard = self._default_write_guard if write_guard is None else write_guard

    @contextmanager
    def _default_write_guard(self):
        path = getattr(self.store, "path", None)
        guard = (self._memory_lock if path is None else
                 self.lock_manager.for_path(Path(path) / ".gestaltdb_temporal_writer.lock"))
        with guard:
            yield

    @contextmanager
    def write_scope(self):
        """Yield this scope or an atomic, separately bound backend transaction.

        A store without transaction support keeps its existing best-effort
        semantics. No implicit nested transaction is opened on a bound child.
        """
        if self.transaction_bound or not getattr(self.store, "supports_transactions", False):
            yield self
            return
        with self._write_guard():
            transaction = self.store.transaction()
            try:
                child = StorageSession(
                    transaction, self.serializer,
                    entity_serializer=self.entity_serializer,
                    indexed_node_properties=set(self.indexed_node_properties),
                    indexed_edge_properties=set(self.indexed_edge_properties),
                    transaction_bound=True, write_guard=self._write_guard,
                    lock_manager=self.lock_manager,
                )
                yield child
            except Exception:
                transaction.rollback()
                raise
            else:
                transaction.commit()
                self.adjacency_counts.clear()


class NodeReader:
    """Current-node read authority over an existing store scope."""

    def __init__(self, session: StorageSession):
        self._session = session

    def get(self, node_id):
        """Read a decoded current node, or None, using its stored byte key."""
        return node_records.get_node(
            self._session.store, self._session.entity_serializer, node_id)

    def get_many(self, node_ids):
        """Read current nodes in request order, retaining None for missing IDs."""
        return node_records.get_nodes(
            self._session.store, self._session.serializer, node_ids)

    def keys(self, num_nodes=None, key_offset=None):
        """Iterate current node keys in backend order."""
        return self._session.store.get_node_keys_generator(num_nodes, key_offset)


class NodeRepository(NodeReader):
    """Current-node CRUD and bulk operations over an explicit storage session.

    Node deletion cascades through current edge records. Temporal history,
    schema DDL, Cypher, rule evaluation and handle lifecycle are not this API.
    A repository borrows its session/store lifetime; it is not a snapshot.
    """

    @property
    def reader(self):
        """Narrow this repository to read-only authority on the same scope."""
        return NodeReader(self._session)

    def put(self, node):
        """Store a current node and maintain indexes in one write scope."""
        with self._session.write_scope() as session:
            node_records.put_node(session.store, session.entity_serializer,
                                  session.indexed_node_properties, node)

    def put_many(self, nodes):
        """Bulk-store current nodes with one transaction and batched indexes."""
        with self._session.write_scope() as session:
            node_records.put_nodes(session.store, session.entity_serializer,
                                   session.indexed_node_properties, nodes)

    def update(self, node_id, new_data, merge_func):
        """Merge properties then put, preserving the legacy read/merge boundary.

        This is not an atomic read-modify-write unless the caller already
        supplied a transaction-bound session.
        """
        node = self.get(node_id)
        if node is None:
            node = Node(node_id=node_id, properties={})
        node.properties = merge_func(node.properties, new_data)
        self.put(node)
        return node

    def delete(self, node_id):
        """Delete a current node and incident edges in one write scope."""
        with self._session.write_scope() as session:
            node_records.delete_node(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_node_properties, session.indexed_edge_properties,
                session.adjacency_counts, node_id,
            )


class EdgeReader:
    """Current-edge read authority over an existing store scope."""

    def __init__(self, session: StorageSession):
        self._session = session

    def get(self, edge_id):
        """Read a decoded current edge, or None, using its stored byte key."""
        return edge_records.get_edge(
            self._session.store, self._session.entity_serializer, edge_id)

    def keys(self, num_edges=None, key_offset=None):
        """Iterate current edge keys in backend order."""
        yield from self._session.store.get_edge_keys_generator(num_edges, key_offset)


class EdgeRepository(EdgeReader):
    """Current-edge CRUD, adjacency and indexes over a borrowed storage session.

    Temporal history and schema DDL are separate capabilities. Replacement
    removes the old edge's indexes and adjacency before publishing the new
    record in the same backend transaction, when supported.
    """

    @property
    def reader(self):
        """Narrow this repository to read-only authority on the same scope."""
        return EdgeReader(self._session)

    def put(self, edge, update_adjacency=True):
        """Store a current edge; optionally omit legacy adjacency blobs."""
        with self._session.write_scope() as session:
            edge_records.put_edge(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts,
                edge, update_adjacency=update_adjacency)

    def put_many(self, edges, check_existing=True):
        """Bulk-store edges, maintaining typed adjacency and secondary indexes.

        Set ``check_existing=False`` only for append-only IDs. This retains
        the existing batched writer rather than looping over object writes.
        """
        with self._session.write_scope() as session:
            ingest_edges.put_edges_bulk(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts,
                edges, check_existing=check_existing)

    def update(self, edge_id, new_data, merge_func):
        """Merge properties then put, preserving the legacy read/merge boundary.

        Read-modify-write is atomic only inside a caller-owned transaction.
        """
        edge = self.get(edge_id)
        if edge is None:
            edge = Edge(edge_id=edge_id, source=None, target=None, properties={})
        edge.properties = merge_func(edge.properties, new_data)
        self.put(edge)
        return edge

    def delete(self, edge_id):
        """Remove a current edge and both adjacency/index representations."""
        with self._session.write_scope() as session:
            edge_records.delete_edge(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts, edge_id)


def _bind_repository(repository_type, previous, store, serializer, configuration):
    """Reuse a repository only while every store-bound dependency is identical.

    The facade supplies a late-bound writer guard, so monkeypatches remain
    effective without making each read allocate a session/codec/thread lock.
    Snapshot copies change stores and cache/config objects, forcing rebinding.
    """
    key = (id(store), id(serializer), *(id(configuration[name]) for name in (
        "entity_serializer", "indexed_node_properties", "indexed_edge_properties",
        "adjacency_counts", "lock_manager",
    )), configuration["transaction_bound"])
    if previous is not None and previous._binding_key == key:
        return previous
    repository = repository_type(StorageSession(store, serializer, **configuration))
    repository._binding_key = key
    return repository


def bind_nodes(previous, store, serializer, **configuration):
    """Bind current-node operations to an exact backend/configuration scope."""
    return _bind_repository(NodeRepository, previous, store, serializer, configuration)


def bind_edges(previous, store, serializer, **configuration):
    """Bind current-edge operations to an exact backend/configuration scope."""
    return _bind_repository(EdgeRepository, previous, store, serializer, configuration)
