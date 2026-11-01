"""Store-bound current-graph sessions and node/edge repositories.

These operations concern mutable graph records, not temporal claims/history.
Use ``graph.nodes``/``graph.edges`` or construct repositories over StorageSession.
See ``docs/architecture.rst`` for a complete example and lifetime boundaries.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any

from ._graph import edge_records, ingest_edges, node_records
from ._graph.entities import Edge, GraphEntityDictSerializer, Node, NodeKeyInput, PropertyMap, key_to_string, node_key_to_bytes
from ._graph.index_maintenance import load_property_index_metadata
from .concurrency import DEFAULT_LOCK_MANAGER

if TYPE_CHECKING:
    from .concurrency import LockManager
    from .kvstores import KVStore
    from .serializers import Serializer


def _borrow_index_names(names: Iterable[str]) -> set[str]:
    """Share a live index-configuration set; copy any other iterable."""
    return names if isinstance(names, set) else set(names)


class StorageSession:
    """Borrow one store scope and own ordinary current-write coordination.

    Codecs and index definitions are explicit; transaction children copy index
    configuration and start with a fresh adjacency cache. The session neither
    closes the caller's store nor initializes identity or temporal history.
    ``write_guard`` allows the owning database to supply its established writer
    coordination; standalone sessions use the same durable writer-lock file.
    """

    def __init__(
        self,
        store: KVStore,
        serializer: Serializer,
        *,
        indexed_node_properties: Iterable[str] | None = None,
        indexed_edge_properties: Iterable[str] | None = None,
        adjacency_counts: dict[bytes, int] | None = None,
        entity_serializer: GraphEntityDictSerializer | None = None,
        transaction_bound: bool = False,
        write_guard: Callable[[], Any] | None = None,
        lock_manager: LockManager | None = None,
    ) -> None:
        self.store: KVStore = store
        self.serializer: Serializer = serializer
        self.entity_serializer = (GraphEntityDictSerializer(serializer)
                                  if entity_serializer is None else entity_serializer)
        # Borrow (never copy) the caller's index-configuration sets: repository
        # sessions share one live set per family with the owning handle, and
        # index DDL publishes new names by mutating that shared set in place.
        # Any other iterable is copied into a fresh set for this session.
        self.indexed_node_properties: set[str] = (
            set(load_property_index_metadata(store, b"schema:indexes:node_properties"))
            if indexed_node_properties is None
            else _borrow_index_names(indexed_node_properties))
        self.indexed_edge_properties: set[str] = (
            set(load_property_index_metadata(store, b"schema:indexes:edge_properties"))
            if indexed_edge_properties is None
            else _borrow_index_names(indexed_edge_properties))
        self.adjacency_counts: dict[bytes, int] = {} if adjacency_counts is None else adjacency_counts
        self.transaction_bound: bool = transaction_bound
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
    def write_scope(self) -> Iterator[StorageSession]:
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

    def __init__(self, session: StorageSession) -> None:
        self._session = session

    def get(self, node_id: NodeKeyInput) -> Node | None:
        """Read a decoded current node, or None, using its stored key.

        Accepts ``str`` or ``bytes``; ``str`` IDs are UTF-8 encoded.
        """
        from ._graph.entities import node_key_to_bytes as _to_bytes

        return node_records.get_node(
            self._session.store, self._session.entity_serializer, _to_bytes(node_id))

    def get_many(self, node_ids: Iterable[NodeKeyInput]) -> list[Node | None]:
        """Read current nodes in request order, retaining None for missing IDs."""
        from ._graph.entities import node_key_to_bytes as _to_bytes

        return node_records.get_nodes(
            self._session.store, self._session.serializer,
            [_to_bytes(node_id) for node_id in node_ids])

    def keys(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Any:
        """Iterate current node keys in backend order."""
        return self._session.store.get_node_keys_generator(num_nodes, key_offset)


class NodeRepository(NodeReader):
    """Current-node CRUD and bulk operations over an explicit storage session.

    Node deletion cascades through current edge records. Temporal history,
    schema DDL, Cypher, rule evaluation and handle lifecycle are not this API.
    A repository borrows its session/store lifetime; it is not a snapshot.
    """

    _binding_key: tuple[Any, ...]

    @property
    def reader(self) -> NodeReader:
        """Narrow this repository to read-only authority on the same scope."""
        return NodeReader(self._session)

    def put(self, node: Node) -> None:
        """Store a current node and maintain indexes in one write scope."""
        with self._session.write_scope() as session:
            node_records.put_node(session.store, session.entity_serializer,
                                  session.indexed_node_properties, node)

    def put_many(self, nodes: Iterable[Node]) -> None:
        """Bulk-store current nodes with one transaction and batched indexes."""
        with self._session.write_scope() as session:
            node_records.put_nodes(session.store, session.entity_serializer,
                                   session.indexed_node_properties, nodes)

    def update(self, node_id: NodeKeyInput, new_data: PropertyMap, merge_func: Callable[[PropertyMap, PropertyMap], PropertyMap]) -> Node:
        """Merge properties then put, preserving the legacy read/merge boundary.

        This is not an atomic read-modify-write unless the caller already
        supplied a transaction-bound session.
        """
        node = self.get(node_key_to_bytes(node_id))
        if node is None:
            node = Node(node_id=key_to_string(node_id), properties={})
        node.properties = merge_func(node.properties, new_data)
        self.put(node)
        return node

    def delete(self, node_id: NodeKeyInput) -> None:
        """Delete a current node and incident edges in one write scope."""
        with self._session.write_scope() as session:
            node_records.delete_node(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_node_properties, session.indexed_edge_properties,
                session.adjacency_counts, node_id,
            )


class EdgeReader:
    """Current-edge read authority over an existing store scope."""

    def __init__(self, session: StorageSession) -> None:
        self._session = session

    def get(self, edge_id: NodeKeyInput) -> Edge | None:
        """Read a decoded current edge, or None, using its stored key.

        Accepts ``str`` or ``bytes``; ``str`` IDs are UTF-8 encoded.
        """
        from ._graph.entities import node_key_to_bytes as _to_bytes

        return edge_records.get_edge(
            self._session.store, self._session.entity_serializer, _to_bytes(edge_id))

    def keys(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Any:
        """Iterate current edge keys in backend order."""
        yield from self._session.store.get_edge_keys_generator(num_edges, key_offset)


class EdgeRepository(EdgeReader):
    """Current-edge CRUD, adjacency and indexes over a borrowed storage session.

    Temporal history and schema DDL are separate capabilities. Replacement
    removes the old edge's indexes and adjacency before publishing the new
    record in the same backend transaction, when supported.
    """

    _binding_key: tuple[Any, ...]

    @property
    def reader(self) -> EdgeReader:
        """Narrow this repository to read-only authority on the same scope."""
        return EdgeReader(self._session)

    def put(self, edge: Edge, update_adjacency: bool = True) -> None:
        """Store a current edge; optionally omit legacy adjacency blobs."""
        with self._session.write_scope() as session:
            edge_records.put_edge(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts,
                edge, update_adjacency=update_adjacency)

    def put_many(self, edges: Iterable[Edge], check_existing: bool = True) -> None:
        """Bulk-store edges, maintaining typed adjacency and secondary indexes.

        Set ``check_existing=False`` only for append-only IDs. This retains
        the existing batched writer rather than looping over object writes.
        """
        with self._session.write_scope() as session:
            ingest_edges.put_edges_bulk(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts,
                edges, check_existing=check_existing)

    def update(self, edge_id: NodeKeyInput, new_data: PropertyMap, merge_func: Callable[[PropertyMap, PropertyMap], PropertyMap]) -> Edge:
        """Merge properties then put, preserving the legacy read/merge boundary.

        Read-modify-write is atomic only inside a caller-owned transaction.
        """
        edge = self.get(node_key_to_bytes(edge_id))
        if edge is None:
            edge = Edge(edge_id=key_to_string(edge_id), source=None, target=None, properties={})
        edge.properties = merge_func(edge.properties, new_data)
        self.put(edge)
        return edge

    def delete(self, edge_id: NodeKeyInput) -> None:
        """Remove a current edge and both adjacency/index representations."""
        with self._session.write_scope() as session:
            edge_records.delete_edge(
                session.store, session.entity_serializer, session.serializer,
                session.indexed_edge_properties, session.adjacency_counts, node_key_to_bytes(edge_id))


def _bind_repository(repository_type: type[NodeRepository] | type[EdgeRepository], previous: NodeRepository | EdgeRepository | None, store: KVStore, serializer: Serializer, configuration: dict[str, Any]) -> NodeRepository | EdgeRepository:
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


def bind_nodes(previous: NodeRepository | None, store: KVStore, serializer: Serializer, **configuration: Any) -> NodeRepository:
    """Bind current-node operations to an exact backend/configuration scope."""
    bound = _bind_repository(NodeRepository, previous, store, serializer, configuration)
    assert isinstance(bound, NodeRepository)
    return bound


def bind_edges(previous: EdgeRepository | None, store: KVStore, serializer: Serializer, **configuration: Any) -> EdgeRepository:
    """Bind current-edge operations to an exact backend/configuration scope."""
    bound = _bind_repository(EdgeRepository, previous, store, serializer, configuration)
    assert isinstance(bound, EdgeRepository)
    return bound
