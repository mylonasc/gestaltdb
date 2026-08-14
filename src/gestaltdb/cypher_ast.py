"""AST objects for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parameter:
    """Cypher query parameter reference, such as ``$name``."""

    name: str


@dataclass(frozen=True)
class PropertyRef:
    """Reference to a variable property, such as ``n.name``."""

    variable: str
    property_name: str


@dataclass(frozen=True)
class ComparisonExpression:
    """Binary comparison expression for the current Cypher subset."""

    left: PropertyRef
    operator: str
    right: object


@dataclass(frozen=True)
class InExpression:
    """Membership predicate, such as ``n.kind IN ["drug"]``."""

    left: PropertyRef
    values: object


@dataclass(frozen=True)
class NullPredicate:
    """Null check predicate."""

    expression: PropertyRef
    negated: bool = False


@dataclass(frozen=True)
class AndExpression:
    """Conjunction of boolean expressions."""

    expressions: tuple[object, ...]


@dataclass(frozen=True)
class OrderItem:
    """One ORDER BY item."""

    expression: str
    descending: bool = False


@dataclass(frozen=True)
class TraversalHop:
    """One typed relationship expansion in a parsed ``MATCH`` pattern."""

    rel_var: str | None
    edge_type: str
    target_var: str
    direction: str = "out"
    edge_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchQuery:
    """Parsed anchored typed path query."""

    source_var: str
    source_id: str
    hops: tuple[TraversalHop, ...]
    returns: tuple[str, ...]
    limit: int | None = None
    where: object | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | None = None
    distinct: bool = False


@dataclass(frozen=True)
class NodePatternClause:
    """One node pattern in a multi-clause ``MATCH`` query."""

    variable: str
    label: str | None = None
    property_name: str | None = None
    property_value: object = None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipPatternClause:
    """One relationship pattern in a multi-clause ``MATCH`` query."""

    source_var: str
    rel_var: str | None
    edge_type: str
    target_var: str
    direction: str = "out"
    edge_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnchoredPatternClause:
    """One anchored traversal pattern in a multi-clause ``MATCH`` query."""

    source_var: str
    source_id: str
    hops: tuple[TraversalHop, ...]


@dataclass(frozen=True)
class MultiMatchQuery:
    """Parsed query containing multiple ``MATCH`` clauses."""

    clauses: tuple[object, ...]
    returns: tuple[str, ...]
    where: object | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | None = None
    limit: int | None = None
    distinct: bool = False


@dataclass(frozen=True)
class SampleTypedPathsCall:
    """Parsed ``pg.sample_typed_paths`` procedure call."""

    seed_ids: list[str]
    pattern: list[dict[str, object]]
    returns: tuple[str, ...] = ("path",)
    limit: int | None = None


@dataclass(frozen=True)
class NodeScanQuery:
    """Parsed indexed node label scan query."""

    variable: str
    label: str | None
    property_name: str | None
    property_value: object
    returns: tuple[str, ...]
    limit: int | None = None
    where: object | None = None
    labels: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | None = None
    distinct: bool = False


@dataclass(frozen=True)
class RelationshipScanQuery:
    """Parsed unanchored typed relationship scan query."""

    source_var: str
    rel_var: str | None
    edge_type: str
    target_var: str
    returns: tuple[str, ...]
    direction: str = "out"
    edge_types: tuple[str, ...] = ()
    where: object | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | None = None
    limit: int | None = None
    distinct: bool = False
