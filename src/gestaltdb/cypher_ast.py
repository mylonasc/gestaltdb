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
class Variable:
    """Reference to a bound Cypher variable."""

    name: str


@dataclass(frozen=True)
class ComparisonExpression:
    """Binary comparison expression for the current Cypher subset."""

    left: object
    operator: str
    right: object


@dataclass(frozen=True)
class InExpression:
    """Membership predicate, such as ``n.kind IN ["drug"]``."""

    left: object
    values: object


@dataclass(frozen=True)
class NullPredicate:
    """Null check predicate."""

    expression: object
    negated: bool = False


@dataclass(frozen=True)
class AndExpression:
    """Conjunction of boolean expressions."""

    expressions: tuple[object, ...]


@dataclass(frozen=True)
class OrExpression:
    """Disjunction of boolean expressions."""

    expressions: tuple[object, ...]


@dataclass(frozen=True)
class XorExpression:
    """Exclusive disjunction of boolean expressions."""

    expressions: tuple[object, ...]


@dataclass(frozen=True)
class NotExpression:
    """Boolean negation."""

    expression: object


@dataclass(frozen=True)
class StringPredicate:
    """Cypher string matching predicate."""

    left: object
    operator: str
    right: object


@dataclass(frozen=True)
class ArithmeticExpression:
    """Binary arithmetic expression."""

    left: object
    operator: str
    right: object


@dataclass(frozen=True)
class UnaryExpression:
    """Unary arithmetic expression."""

    operator: str
    expression: object


@dataclass(frozen=True)
class ListExpression:
    """List literal containing one or more non-literal expressions."""

    items: tuple[object, ...]


@dataclass(frozen=True)
class MapExpression:
    """Map literal containing one or more non-literal expressions."""

    items: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class OrderItem:
    """One ORDER BY item."""

    expression: str
    descending: bool = False
    expression_ast: object | None = None


@dataclass(frozen=True)
class TraversalHop:
    """One typed relationship expansion in a parsed ``MATCH`` pattern."""

    rel_var: str | None
    edge_type: str
    target_var: str
    direction: str = "out"
    edge_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodePattern:
    """A node in a generalized fixed-length pattern."""

    variable: str | None
    labels: tuple[str, ...] = ()
    properties: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class PatternHop:
    """One relationship and target node in a generalized path pattern."""

    rel_var: str | None
    edge_types: tuple[str, ...]
    target: NodePattern
    direction: str = "out"


@dataclass(frozen=True)
class PathPatternClause:
    """A generalized fixed-length path pattern."""

    source: NodePattern
    hops: tuple[PatternHop, ...]


@dataclass(frozen=True)
class MatchQuery:
    """Parsed anchored typed path query."""

    source_var: str
    source_id: str
    hops: tuple[TraversalHop, ...]
    returns: tuple[str, ...]
    limit: int | Parameter | None = None
    where: object | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[object, ...] = ()


@dataclass(frozen=True)
class NodePatternClause:
    """One node pattern in a multi-clause ``MATCH`` query."""

    variable: str
    label: str | None = None
    property_name: str | None = None
    property_value: object = None
    labels: tuple[str, ...] = ()
    properties: tuple[tuple[str, object], ...] = ()


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
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[object, ...] = ()
    match_group_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SampleTypedPathsCall:
    """Parsed ``pg.sample_typed_paths`` procedure call."""

    seed_ids: list[str]
    pattern: list[dict[str, object]]
    returns: tuple[str, ...] = ("path",)
    limit: int | Parameter | None = None


@dataclass(frozen=True)
class NodeScanQuery:
    """Parsed indexed node label scan query."""

    variable: str
    label: str | None
    property_name: str | None
    property_value: object
    returns: tuple[str, ...]
    limit: int | Parameter | None = None
    where: object | None = None
    labels: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    distinct: bool = False
    properties: tuple[tuple[str, object], ...] = ()
    projection_expressions: tuple[object, ...] = ()


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
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[object, ...] = ()
