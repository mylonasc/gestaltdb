"""Structural scope a graph handle must provide for Cypher execution.

``GraphQueryScope`` is the narrow contract consumed by
:mod:`gestaltdb.query_engine.cypher.runtime` and
:mod:`gestaltdb.query_engine.cypher.procedures`: entity codecs, decoded
reads, index-backed traversal reads, snapshot identity, sampling entry points, and
the temporal/epistemic services procedures need. ``GraphDB`` satisfies it
directly; transaction children rebind the same facade; read-only views
satisfy it through their scoped readers and attribute forwarding. It is a
:class:`~typing.Protocol`, so no implementation inherits from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..._graph.entities import Edge, Node, NodeKeyInput
    from ...epistemic_engine import EpistemicEngine
    from ...hierarchy import RelationHierarchy
    from ...sampling import SamplingHop, SamplingPattern
    from ...temporal_engine import TemporalEngine
    from ...traversal import Direction, RandomLike, SampledPath, TypedAdjacencyRecord


class GraphQueryScope(Protocol):
    """Executable graph surface required by one Cypher query."""

    def node_key_to_bytes(self, node_key: NodeKeyInput) -> bytes: ...
    def key_to_string(self, key: NodeKeyInput) -> str: ...
    def get_node(self, node_id: bytes) -> Node | None: ...
    def get_edge(self, edge_id: bytes) -> Edge | None: ...
    def get_node_keys_generator(
        self, num_nodes: int | None = None, key_offset: bytes | None = None
    ) -> Iterator[bytes]: ...
    def iter_typed_adjacency(
        self, node_id: bytes, edge_type: str, direction: Direction = "out"
    ) -> Iterator[TypedAdjacencyRecord]: ...
    def iter_node_ids_by_label(self, label: str) -> Iterator[bytes]: ...
    def iter_node_ids_by_property(self, property_name: str, value: object) -> Iterator[bytes]: ...
    def iter_node_ids_by_label_property(
        self, label: str, property_name: str, value: object
    ) -> Iterator[bytes]: ...
    def iter_node_ids_by_property_range(
        self,
        property_name: str,
        start_value: object = None,
        end_value: object = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> Iterator[bytes]: ...
    def iter_node_ids_by_label_property_range(
        self,
        label: str,
        property_name: str,
        start_value: object = None,
        end_value: object = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> Iterator[bytes]: ...
    def iter_edge_ids(
        self, num_edges: int | None = None, key_offset: bytes | None = None
    ) -> Iterator[bytes]: ...
    def iter_edge_ids_by_type(self, edge_type: str) -> Iterator[bytes]: ...
    def iter_edge_ids_by_type_property(
        self, edge_type: str, property_name: str, value: object
    ) -> Iterator[bytes]: ...
    def iter_edge_ids_by_property_range(
        self,
        property_name: str,
        start_value: object = None,
        end_value: object = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> Iterator[bytes]: ...
    def iter_edge_ids_by_type_property_range(
        self,
        edge_type: str,
        property_name: str,
        start_value: object = None,
        end_value: object = None,
        include_start: bool = True,
        include_end: bool = True,
    ) -> Iterator[bytes]: ...
    @property
    def indexed_node_properties(self) -> set[str]: ...
    def sample_typed_paths(
        self,
        seed_ids: Iterable[NodeKeyInput],
        pattern: SamplingPattern | Iterable[SamplingHop | Mapping[str, object]],
        rng: RandomLike | None = None,
        *,
        relation_hierarchy: RelationHierarchy | None = None,
    ) -> list[SampledPath]: ...
    @property
    def manifest(self) -> Mapping[str, Any]: ...
    @property
    def database_id(self) -> str: ...
    @property
    def temporal(self) -> TemporalEngine: ...
    @property
    def epistemic(self) -> EpistemicEngine: ...
