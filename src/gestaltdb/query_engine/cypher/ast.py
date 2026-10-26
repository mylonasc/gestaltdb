"""AST objects for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open source range and its one-based line and column positions."""

    start_offset: int
    end_offset: int
    line: int
    column: int
    end_line: int
    end_column: int

    @property
    def start(self) -> int:
        """Return the inclusive source offset."""
        return self.start_offset

    @property
    def end(self) -> int:
        """Return the exclusive source offset."""
        return self.end_offset


@dataclass(frozen=True)
class Query:
    """Canonical Cypher query represented as an ordered clause sequence."""

    clauses: tuple[Clause, ...]
    source: str
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class MatchClause:
    """One textual ``MATCH`` containing one or more comma-separated patterns."""

    patterns: tuple[PathPatternClause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)
    selector: PathSelector | None = None
    qualifiers: tuple[TemporalQualifier, ...] = ()


@dataclass(frozen=True)
class TemporalQualifier:
    """One query-wide valid-time or system-time ``AS OF`` expression."""

    kind: Literal["valid", "system"]
    expression: Expression
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class PathSelector:
    """A shortest-path selector for a ``MATCH`` clause.

    ``mode`` is ``"any"`` (up to ``limit`` shortest paths, ``limit`` always
    set) or ``"all"`` (every path at the minimal matching length).
    """

    mode: Literal["any", "all"]
    limit: int | None = None


@dataclass(frozen=True)
class OptionalMatchClause:
    """One textual ``OPTIONAL MATCH`` with left-outer-join semantics."""

    patterns: tuple[PathPatternClause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)
    selector: PathSelector | None = None
    qualifiers: tuple[TemporalQualifier, ...] = ()


@dataclass(frozen=True)
class UnwindClause:
    """One ``UNWIND <expression> AS <variable>`` list-to-rows expansion."""

    expression: Expression
    variable: str
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SetProperty:
    """A ``SET n.prop = <expression>`` item."""

    variable: str
    property_name: str
    expression: Expression


@dataclass(frozen=True)
class SetLabels:
    """A ``SET n:Label`` item."""

    variable: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class SetMerge:
    """A ``SET n += <map>`` item merging properties."""

    variable: str
    expression: Expression


@dataclass(frozen=True)
class SetReplace:
    """A ``SET n = <map>`` item replacing all properties."""

    variable: str
    expression: Expression


@dataclass(frozen=True)
class RemoveProperty:
    """A ``REMOVE n.prop`` item."""

    variable: str
    property_name: str


@dataclass(frozen=True)
class RemoveLabels:
    """A ``REMOVE n:Label`` item."""

    variable: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class CreateClause:
    """One ``CREATE`` with comma-separated patterns creating nodes and edges."""

    patterns: tuple[PathPatternClause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SetClause:
    """One ``SET`` with comma-separated mutation items."""

    items: tuple[SetItem, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class RemoveClause:
    """One ``REMOVE`` with comma-separated removal items."""

    items: tuple[RemoveItem, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DeleteClause:
    """One ``DELETE`` or ``DETACH DELETE`` over entity expressions."""

    expressions: tuple[Expression, ...]
    detach: bool = False
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class MergeClause:
    """One ``MERGE`` matching a pattern or creating it, with ``ON`` actions."""

    patterns: tuple[PathPatternClause, ...]
    on_create: tuple[SetItem, ...] = ()
    on_match: tuple[SetItem, ...] = ()
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ForeachClause:
    """One ``FOREACH (x IN list | ...)`` write-only loop over body clauses."""

    variable: str
    iterable: Expression
    body: tuple[Clause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class CreateConstraint:
    """A uniqueness or existence constraint over one node label property."""

    label: str
    property_name: str
    kind: Literal["unique", "exists"]
    name: str | None = None


@dataclass(frozen=True)
class DropConstraint:
    """Drop a constraint by name."""

    name: str


@dataclass(frozen=True)
class ShowConstraints:
    """List the constraint catalog."""


@dataclass(frozen=True)
class ShowIndexes:
    """List configured node and relationship property indexes."""


@dataclass(frozen=True)
class SubqueryClause:
    """One ``CALL { ... }`` correlated subquery over an inner clause query."""

    query: Query
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class UnionQuery:
    """Two or more branch queries combined with ``UNION [ALL]``.

    ``union_all[i]`` selects the operator between ``branches[i]`` and
    ``branches[i + 1]``: ``True`` for ``UNION ALL`` (concatenate),
    ``False`` for ``UNION`` (deduplicate). Evaluation folds left, so each
    ``UNION`` step deduplicates everything accumulated so far.
    """

    branches: tuple[Query, ...]
    union_all: tuple[bool, ...]
    source: str
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class PathValue:
    """An immutable matched path: endpoint-to-endpoint nodes and edges.

    Housed with the AST because every layer (parser, planner, runtime,
    functions) needs it without import cycles.
    """

    nodes: tuple[object, ...]
    edges: tuple[object, ...]

    @property
    def length(self) -> int:
        """Return the number of relationships in the path."""
        return len(self.edges)


@dataclass(frozen=True)
class WhereClause:
    """A filter in its textual position in the query."""

    expression: Expression
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Wildcard:
    """A projection wildcard."""

    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class FunctionCall:
    """A function call such as ``count(*)`` or ``collect(n.value)``."""

    name: str
    arguments: tuple[Expression, ...] = ()
    distinct: bool = False
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ProjectionItem:
    """One projection expression and its optional output alias."""

    expression: Expression
    alias: str | None = None
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class WithClause:
    """An intermediate projection, scope boundary, and local modifiers."""

    items: tuple[ProjectionItem, ...]
    distinct: bool = False
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    span: SourceSpan | None = field(default=None, compare=False, repr=False)

    @property
    def projections(self) -> tuple[ProjectionItem, ...]:
        """Return projection items using the planner-oriented name."""
        return self.items


@dataclass(frozen=True)
class ReturnClause:
    """A terminal projection and the result modifiers owned by it."""

    items: tuple[ProjectionItem, ...]
    distinct: bool = False
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    span: SourceSpan | None = field(default=None, compare=False, repr=False)

    @property
    def projections(self) -> tuple[ProjectionItem, ...]:
        """Return projection items using the planner-oriented name."""
        return self.items


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

    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True)
class InExpression:
    """Membership predicate, such as ``n.kind IN ["drug"]``."""

    left: Expression
    values: Expression


@dataclass(frozen=True)
class NullPredicate:
    """Null check predicate."""

    expression: Expression
    negated: bool = False


@dataclass(frozen=True)
class AndExpression:
    """Conjunction of boolean expressions."""

    expressions: tuple[Expression, ...]


@dataclass(frozen=True)
class OrExpression:
    """Disjunction of boolean expressions."""

    expressions: tuple[Expression, ...]


@dataclass(frozen=True)
class XorExpression:
    """Exclusive disjunction of boolean expressions."""

    expressions: tuple[Expression, ...]


@dataclass(frozen=True)
class NotExpression:
    """Boolean negation."""

    expression: Expression


@dataclass(frozen=True)
class StringPredicate:
    """Cypher string matching predicate."""

    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True)
class ArithmeticExpression:
    """Binary arithmetic expression."""

    left: Expression
    operator: str
    right: Expression


@dataclass(frozen=True)
class UnaryExpression:
    """Unary arithmetic expression."""

    operator: str
    expression: Expression


@dataclass(frozen=True)
class ListExpression:
    """List literal containing one or more non-literal expressions."""

    items: tuple[Expression, ...]


@dataclass(frozen=True)
class MapExpression:
    """Map literal containing one or more non-literal expressions."""

    items: tuple[tuple[str, Expression], ...]


@dataclass(frozen=True)
class CaseExpression:
    """A ``CASE`` conditional expression.

    ``operand`` is ``None`` for generic ``CASE WHEN`` and the compared value
    for simple ``CASE <operand> WHEN``. ``whens`` holds ``(condition, value)``
    pairs where the condition is a boolean expression (generic) or a compared
    value (simple). ``else_value`` is ``None`` when no ``ELSE`` is present.
    """

    whens: tuple[tuple[Expression, Expression], ...]
    else_value: Expression | None = None
    operand: Expression | None = None


@dataclass(frozen=True)
class SubscriptExpression:
    """Dynamic element access such as ``xs[0]``, ``m[key]`` or ``n[$prop]``."""

    target: Expression
    index: Expression


@dataclass(frozen=True)
class SliceExpression:
    """List slicing such as ``xs[1..3]``; ``start``/``end`` may be ``None``."""

    target: Expression
    start: Expression | None = None
    end: Expression | None = None


@dataclass(frozen=True)
class ListComprehension:
    """A list comprehension such as ``[x IN xs WHERE p | f(x)]``."""

    variable: str
    iterable: Expression
    where: Expression | None = None
    projection: Expression | None = None


@dataclass(frozen=True)
class ReduceExpression:
    """A reduction such as ``reduce(acc = 0, x IN xs | acc + x)``."""

    accumulator: str
    initial: Expression
    variable: str
    iterable: Expression
    expression: Expression


@dataclass(frozen=True)
class MapProjectionExpression:
    """A map projection such as ``n{.*, .age, name: n.name}``.

    Items are ``("all",)`` for ``.*``, ``("property", name)`` for ``.name``,
    and ``("alias", key, expression)`` for ``key: expression``.
    """

    variable: str
    items: tuple[tuple, ...]


@dataclass(frozen=True)
class QuantifiedPredicate:
    """A quantified predicate such as ``all(x IN xs WHERE p)``."""

    function: str
    variable: str
    iterable: Expression
    where: Expression


@dataclass(frozen=True)
class ExistsExpression:
    """An ``exists(n.property)`` existence check."""

    expression: Expression


@dataclass(frozen=True)
class PropertyAccessExpression:
    """Property access on a computed value, such as ``startNode(r).name``."""

    expression: Expression
    property_name: str


@dataclass(frozen=True)
class ShortestPathExpression:
    """A ``shortestPath``/``allShortestPaths`` call over a single-hop pattern.

    Evaluates to a node list (single shortest path) or a list of node lists,
    or ``None``/``[]`` when nothing matches. Both pattern endpoints must be
    bound variables; binding new variables requires ``MATCH SHORTEST``.
    """

    pattern: PathPatternClause
    all_paths: bool = False


@dataclass(frozen=True)
class OrderItem:
    """One ORDER BY item."""

    expression: str
    descending: bool = False
    expression_ast: Expression | None = None


@dataclass(frozen=True)
class TraversalHop:
    """One typed relationship expansion in a parsed ``MATCH`` pattern."""

    rel_var: str | None
    edge_type: str
    target_var: str
    direction: Literal["out", "in", "any"] = "out"
    edge_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodePattern:
    """A node in a generalized fixed-length pattern."""

    variable: str | None
    labels: tuple[str, ...] = ()
    properties: tuple[tuple[str, object], ...] = ()
    label_expression: tuple[tuple[frozenset[str], frozenset[str]], ...] | None = None


@dataclass(frozen=True)
class PatternHop:
    """One relationship and target node in a generalized path pattern.

    Fixed hops use ``(min_length, max_length) == (1, 1)``. Variable-length
    hops match ``min_length..max_length`` traversals (``max_length`` of
    ``None`` is unbounded) with trail semantics: no relationship repeats
    within one expansion. Multi-edge matches bind ``rel_var`` to a list of
    edges, or ``[]`` for zero-length matches.
    """

    rel_var: str | None
    edge_types: tuple[str, ...]
    target: NodePattern
    direction: Literal["out", "in", "any"] = "out"
    properties: tuple[tuple[str, object], ...] = ()
    min_length: int = 1
    max_length: int | None = 1


@dataclass(frozen=True)
class PathPatternClause:
    """A generalized fixed-length path pattern.

    ``name`` binds the whole matched path (``p = (a)-->(b)``) to a path
    value holding endpoint-to-endpoint nodes and edges.
    """

    source: NodePattern
    hops: tuple[PatternHop, ...]
    name: str | None = None


@dataclass(frozen=True)
class MatchQuery:
    """Parsed anchored typed path query."""

    source_var: str
    source_id: str
    hops: tuple[TraversalHop, ...]
    returns: tuple[str, ...]
    limit: int | Parameter | None = None
    where: Expression | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[Expression, ...] = ()


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
    direction: Literal["out", "in", "any"] = "out"
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

    clauses: tuple[Clause, ...]
    returns: tuple[str, ...]
    where: Expression | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[Expression, ...] = ()
    match_group_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SampleTypedPathsCall:
    """Parsed ``pg.sample_typed_paths`` procedure call."""

    seed_ids: object
    pattern: object
    returns: tuple[str, ...] = ("path",)
    limit: int | Parameter | None = None
    output_name: str = "path"


@dataclass(frozen=True)
class EntailsCall:
    """Parsed allowlisted ``kg.entails`` procedure call."""

    agent: object
    proposition: object
    mode: object
    options: object
    yields: tuple[tuple[str, str], ...]
    returns: tuple[str, ...]
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
    where: Expression | None = None
    labels: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    distinct: bool = False
    properties: tuple[tuple[str, object], ...] = ()
    projection_expressions: tuple[Expression, ...] = ()


@dataclass(frozen=True)
class RelationshipScanQuery:
    """Parsed unanchored typed relationship scan query."""

    source_var: str
    rel_var: str | None
    edge_type: str
    target_var: str
    returns: tuple[str, ...]
    direction: Literal["out", "in", "any"] = "out"
    edge_types: tuple[str, ...] = ()
    where: Expression | None = None
    projections: tuple[str, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    skip: int | Parameter | None = None
    limit: int | Parameter | None = None
    distinct: bool = False
    projection_expressions: tuple[Expression, ...] = ()


ScalarValue = Union[str, int, float, bool, None]
"""Inline literal that may appear directly in an expression position."""

Expression = Union[
    Parameter,
    PropertyRef,
    Variable,
    FunctionCall,
    ComparisonExpression,
    InExpression,
    NullPredicate,
    AndExpression,
    OrExpression,
    XorExpression,
    NotExpression,
    StringPredicate,
    ArithmeticExpression,
    UnaryExpression,
    ListExpression,
    MapExpression,
    CaseExpression,
    SubscriptExpression,
    SliceExpression,
    ListComprehension,
    ReduceExpression,
    MapProjectionExpression,
    QuantifiedPredicate,
    ExistsExpression,
    PropertyAccessExpression,
    ShortestPathExpression,
    ScalarValue,
]
"""Anything that may appear where a Cypher value expression is expected.

Structured nodes are the parsed AST; ``ScalarValue`` covers inline literals
the parser leaves as plain Python values. Property-map values stay ``object``
because temporal scalars may appear there.
"""

SetItem = Union[SetProperty, SetLabels, SetMerge, SetReplace]
"""One ``SET`` mutation item, including ``ON CREATE``/``ON MATCH`` actions."""

RemoveItem = Union[RemoveProperty, RemoveLabels]
"""One ``REMOVE`` item."""

Clause = Union[
    MatchClause,
    OptionalMatchClause,
    WhereClause,
    UnwindClause,
    SubqueryClause,
    CreateClause,
    SetClause,
    RemoveClause,
    DeleteClause,
    MergeClause,
    ForeachClause,
    WithClause,
    ReturnClause,
]
"""One ordered step of a canonical clause query."""
