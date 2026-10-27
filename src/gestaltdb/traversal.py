"""Current typed-adjacency traversal and live reservoir sampling.

``AdjacencyReader`` grants traversal-read authority over one ``StorageSession``;
``TraversalService`` adds adjacency rebuilds and live sampling. Neither needs a
GraphDB, so transaction/snapshot scopes bind naturally and snapshot views can
expose the narrow reader. Snapshot construction and array-native sampling stay
in their own modules.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Literal, Protocol

from ._graph import adjacency as _adjacency
from ._graph import index_maintenance as _index_maintenance
from ._graph import sampling as _sampling
from ._graph.entities import Node, NodeKeyInput, node_key_to_bytes
from ._graph.sampling import SampledPath, SampledSubgraph, TypedAdjacencyRecord
from .current import StorageSession
from .indexes import STALE_KEY
from .sampling import RelationHierarchy, SamplingHop, SamplingPattern

Direction = Literal["out", "in", "any"]


class RandomLike(Protocol):
    """Minimal random interface required by live reservoir sampling."""

    def randrange(self, n: int) -> int: ...


class AdjacencyReader:
    """Typed adjacency reads over an existing store scope."""

    def __init__(self, session: StorageSession) -> None:
        self._session = session

    def get_typed_adjacency(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out") -> list[TypedAdjacencyRecord]:
        """Return typed adjacency records for one node, type and direction."""
        return _adjacency.get_typed_adjacency(
            self._session.store, node_key_to_bytes(node_id), edge_type, direction)

    def count_typed_adjacency(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out") -> int:
        """Count typed adjacency records using this scope's count cache."""
        return _adjacency.count_typed_adjacency(
            self._session.store, self._session.adjacency_counts,
            node_key_to_bytes(node_id), edge_type, direction)

    def iter_typed_adjacency(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out") -> Iterator[TypedAdjacencyRecord]:
        """Yield typed adjacency records without materializing a list."""
        return _adjacency.iter_typed_adjacency(
            self._session.store, node_key_to_bytes(node_id), edge_type, direction)

    def neighbors_by_edge_type(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out") -> list[bytes]:
        """Return neighbor IDs reached through one edge type."""
        return _adjacency.neighbors_by_edge_type(
            self._session.store, node_key_to_bytes(node_id), edge_type, direction)

    def edges_by_edge_type(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out") -> list[bytes]:
        """Return edge IDs reached through one edge type."""
        return _adjacency.edges_by_edge_type(
            self._session.store, node_key_to_bytes(node_id), edge_type, direction)


class TraversalService(AdjacencyReader):
    """Current adjacency rebuilds and live sampling for one store scope.

    Sampling scans the bound store directly. ``sample_nodes`` keeps the existing
    label-index freshness check, so deferred indexes still fail closed instead
    of silently sampling a stale candidate set.
    """

    @property
    def reader(self) -> AdjacencyReader:
        """Narrow this service to read-only traversal on the same scope."""
        return AdjacencyReader(self._session)

    def rebuild_typed_adjacency(self) -> int:
        """Rebuild typed adjacency records from stored current edges."""
        with self._session.write_scope() as session:
            return _adjacency.rebuild_typed_adjacency(
                session.store, session.entity_serializer, session.adjacency_counts)

    def _ensure_index_current(self, *families: str) -> None:
        _index_maintenance.ensure_indexes_current(
            self._session.store, STALE_KEY, *families)

    def sample_nodes(self, sample_size: int, *, label: str | None = None,
                     node_filter: Callable[[Node], bool] | None = None, rng: RandomLike | None = None) -> list[str]:
        """Reservoir-sample live node IDs, bounded by ``sample_size``."""
        return _sampling.sample_nodes(
            self._session.store, self._session.entity_serializer,
            self._ensure_index_current, sample_size,
            label=label, node_filter=node_filter, rng=rng)

    def sample_neighbors(self, node_id: NodeKeyInput, edge_type: str, direction: Direction = "out",
                         sample_size: int = 10, rng: RandomLike | None = None, *,
                         relation_hierarchy: RelationHierarchy | None = None) -> list[TypedAdjacencyRecord]:
        """Reservoir-sample typed adjacency records for one node."""
        return _sampling.sample_neighbors(
            self._session.store, node_id, edge_type, direction=direction,
            sample_size=sample_size, rng=rng, relation_hierarchy=relation_hierarchy)

    def sample_typed_paths(self, seed_ids: Iterable[NodeKeyInput], pattern: SamplingPattern | Iterable[SamplingHop | Mapping[str, object]],
                           rng: RandomLike | None = None, *, relation_hierarchy: RelationHierarchy | None = None) -> list[SampledPath]:
        """Sample ordered typed paths from each seed."""
        return _sampling.sample_typed_paths(
            self._session.store, seed_ids, pattern, rng=rng,
            relation_hierarchy=relation_hierarchy)

    def sample_typed_subgraph(self, seed_ids: Iterable[NodeKeyInput], pattern: SamplingPattern | Iterable[SamplingHop | Mapping[str, object]],
                              rng: RandomLike | None = None, *, relation_hierarchy: RelationHierarchy | None = None) -> SampledSubgraph:
        """Sample and materialize a typed subgraph around seed nodes."""
        return _sampling.sample_typed_subgraph(
            self._session.store, self._session.entity_serializer, seed_ids, pattern,
            rng=rng, relation_hierarchy=relation_hierarchy)
