"""AST objects for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    clauses: tuple[object, ...]
    source: str
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class MatchClause:
    """One textual ``MATCH`` containing one or more comma-separated patterns."""

    patterns: tuple[PathPatternClause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class OptionalMatchClause:
    """One textual ``OPTIONAL MATCH`` with left-outer-join semantics."""

    patterns: tuple[PathPatternClause, ...]
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class UnwindClause:
    """One ``UNWIND <expression> AS <variable>`` list-to-rows expansion."""

    expression: object
    variable: str
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


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
class WhereClause:
    """A filter in its textual position in the query."""

    expression: object
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Wildcard:
    """A projection wildcard."""

    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class FunctionCall:
    """A function call such as ``count(*)`` or ``collect(n.value)``."""

    name: str
    arguments: tuple[object, ...] = ()
    distinct: bool = False
    span: SourceSpan | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ProjectionItem:
    """One projection expression and its optional output alias."""

    expression: object
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
class CaseExpression:
    """A ``CASE`` conditional expression.

    ``operand`` is ``None`` for generic ``CASE WHEN`` and the compared value
    for simple ``CASE <operand> WHEN``. ``whens`` holds ``(condition, value)``
    pairs where the condition is a boolean expression (generic) or a compared
    value (simple). ``else_value`` is ``None`` when no ``ELSE`` is present.
    """

    whens: tuple[tuple[object, object], ...]
    else_value: object = None
    operand: object | None = None


@dataclass(frozen=True)
class SubscriptExpression:
    """Dynamic element access such as ``xs[0]``, ``m[key]`` or ``n[$prop]``."""

    target: object
    index: object


@dataclass(frozen=True)
class SliceExpression:
    """List slicing such as ``xs[1..3]``; ``start``/``end`` may be ``None``."""

    target: object
    start: object | None = None
    end: object | None = None


@dataclass(frozen=True)
class ListComprehension:
    """A list comprehension such as ``[x IN xs WHERE p | f(x)]``."""

    variable: str
    iterable: object
    where: object | None = None
    projection: object | None = None


@dataclass(frozen=True)
class ReduceExpression:
    """A reduction such as ``reduce(acc = 0, x IN xs | acc + x)``."""

    accumulator: str
    initial: object
    variable: str
    iterable: object
    expression: object


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
    iterable: object
    where: object


@dataclass(frozen=True)
class ExistsExpression:
    """An ``exists(n.property)`` existence check."""

    expression: object


@dataclass(frozen=True)
class PropertyAccessExpression:
    """Property access on a computed value, such as ``startNode(r).name``."""

    expression: object
    property_name: str


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
    properties: tuple[tuple[str, object], ...] = ()


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
