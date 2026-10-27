"""Internal Cypher execution records and narrow structural entity contracts.

The runtime intentionally accepts node-shaped objects in bindings. These
protocols preserve that boundary without claiming that every such object is
a concrete database Node. Write capabilities stay separate from read scopes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Mapping, Protocol, Sequence, TypeAlias, TypedDict

from .scope import GraphQueryScope

if TYPE_CHECKING:
    from ..._graph.entities import Edge, Node
    from .runtime import BindingRow


class EntityShape(Protocol):
    @property
    def get_id(self) -> str: ...
    @property
    def properties(self) -> Mapping[str, object]: ...


class NodeShape(EntityShape, Protocol):
    @property
    def labels(self) -> Sequence[str]: ...


class EdgeShape(EntityShape, Protocol):
    @property
    def source(self) -> str | Node | None: ...
    @property
    def target(self) -> str | Node | None: ...


class GraphWriteScope(GraphQueryScope, Protocol):
    node_constraints: list[dict[str, str]]

    def put_node(self, node: Node) -> None: ...
    def put_edge(self, edge: Edge) -> None: ...
    def delete_node(self, node_id: bytes) -> None: ...
    def delete_edge(self, edge_id: bytes) -> None: ...


PropertyItems: TypeAlias = tuple[tuple[str, object], ...]
NodeRangeBounds: TypeAlias = tuple[str, object, object, bool, bool]
ExactSeek: TypeAlias = tuple[Literal["exact"], str, object]
RangeSeek: TypeAlias = tuple[Literal["range"], str, object, object, bool, bool]
EdgeSeek: TypeAlias = ExactSeek | RangeSeek
NamedTrail: TypeAlias = "tuple[BindingRow, list[NodeShape | None], list[Edge]]"
BfsTrail: TypeAlias = "tuple[bytes, Node, list[Edge], frozenset[bytes], list[bytes]]"


class AggregateGroup(TypedDict):
    key_values: list[object]
    row_count: int
    arguments: list[list[object]]


class ProjectionMetadata(Protocol):
    @property
    def returns(self) -> tuple[str, ...]: ...
