"""Clause-aware semantic analysis for the canonical Cypher AST."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

from .cypher_ast import (
    AndExpression,
    ArithmeticExpression,
    CaseExpression,
    ComparisonExpression,
    ExistsExpression,
    FunctionCall,
    InExpression,
    ListComprehension,
    ListExpression,
    MapExpression,
    MapProjectionExpression,
    MatchClause,
    NotExpression,
    NullPredicate,
    OptionalMatchClause,
    OrExpression,
    Parameter,
    ProjectionItem,
    PropertyAccessExpression,
    PropertyRef,
    QuantifiedPredicate,
    Query,
    ReduceExpression,
    ReturnClause,
    SliceExpression,
    SourceSpan,
    StringPredicate,
    SubscriptExpression,
    UnaryExpression,
    Variable,
    WhereClause,
    Wildcard,
    WithClause,
    XorExpression,
)
from .cypher_errors import CypherSemanticError
from .cypher_functions import (
    check_scalar_arity,
    get_function_def,
    is_aggregate_function,
)


class SymbolKind(str, Enum):
    """The known runtime kind of a scoped Cypher symbol."""

    NODE = "node"
    RELATIONSHIP = "relationship"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class Symbol:
    """One named definition in a scope."""

    name: str
    kind: SymbolKind
    span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    """An immutable scope preserving symbol introduction order."""

    symbols: tuple[Symbol, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """Return symbol names in deterministic introduction order."""
        return tuple(symbol.name for symbol in self.symbols)

    def resolve(self, name: str) -> Symbol | None:
        """Resolve a name in this scope."""
        return next((symbol for symbol in self.symbols if symbol.name == name), None)


@dataclass(frozen=True, slots=True)
class ResolvedProjection:
    """A projection expression with its resolved output definition."""

    item: ProjectionItem
    expression: object
    output: Symbol
    rendered_expression: str


@dataclass(frozen=True, slots=True)
class ClauseAnalysis:
    """Scopes at one clause boundary and any resolved projections."""

    clause: object
    scope_before: Scope
    scope_after: Scope
    projections: tuple[ResolvedProjection, ...] = ()

    @property
    def output_names(self) -> tuple[str, ...]:
        """Return projection output names for this clause."""
        return tuple(projection.output.name for projection in self.projections)


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    """Immutable semantic result for a canonical query."""

    query: Query
    clauses: tuple[ClauseAnalysis, ...]

    @property
    def clause_scopes(self) -> tuple[Scope, ...]:
        """Return each clause's outgoing scope in textual order."""
        return tuple(clause.scope_after for clause in self.clauses)

    @property
    def final_scope(self) -> Scope:
        """Return the terminal projection scope."""
        return self.clauses[-1].scope_after if self.clauses else Scope()

    @property
    def output_names(self) -> tuple[str, ...]:
        """Return terminal query column names."""
        return self.clauses[-1].output_names if self.clauses else ()


def analyze_query(query: Query) -> QueryAnalysis:
    """Analyze a canonical query left-to-right and resolve its scopes."""
    scope = Scope()
    snapshots: list[ClauseAnalysis] = []
    previous: object | None = None

    for clause in query.clauses:
        incoming = scope
        projections: tuple[ResolvedProjection, ...] = ()
        if isinstance(clause, (MatchClause, OptionalMatchClause)):
            scope = _analyze_match(clause, scope, query.source)
        elif isinstance(clause, WhereClause):
            if not isinstance(previous, (MatchClause, OptionalMatchClause, WithClause)):
                _raise_semantic("WHERE must immediately follow MATCH or WITH", query.source, clause.span)
            _validate_expression(clause.expression, scope, query.source, "WHERE", clause.span)
            validate_function_calls(clause.expression, query.source, clause.span, allow_aggregate=False)
        elif isinstance(clause, (WithClause, ReturnClause)):
            label = "WITH" if isinstance(clause, WithClause) else "RETURN"
            projections = _resolve_projections(clause, scope, query.source, label)
            scope = Scope(tuple(projection.output for projection in projections))
            order_scope = _merge_scopes(incoming, scope)
            for item in clause.order_by:
                _validate_expression(item.expression_ast, order_scope, query.source, "ORDER BY", clause.span)
                validate_function_calls(item.expression_ast, query.source, clause.span, allow_aggregate=True)
            if not any(
                isinstance(projection.expression, FunctionCall)
                and is_aggregate_function(projection.expression.name)
                for projection in projections
            ):
                for item in clause.order_by:
                    if contains_aggregate_call(item.expression_ast):
                        _raise_semantic(
                            "ORDER BY aggregates require an aggregate projection",
                            query.source,
                            clause.span,
                        )
        else:
            _raise_semantic("Unsupported Cypher clause", query.source, getattr(clause, "span", None))

        snapshots.append(ClauseAnalysis(clause, incoming, scope, projections))
        previous = clause

    if not query.clauses or not isinstance(query.clauses[-1], ReturnClause):
        span = query.clauses[-1].span if query.clauses else query.span
        _raise_semantic("Cypher query must terminate with RETURN", query.source, span)
    return QueryAnalysis(query, tuple(snapshots))


def expression_variables(expression: object) -> tuple[str, ...]:
    """Return variable references in expression traversal order."""
    if expression is None or isinstance(expression, (str, int, float, bool, Parameter, Wildcard)):
        return ()
    if isinstance(expression, Variable):
        return (expression.name,)
    if isinstance(expression, PropertyRef):
        return (expression.variable,)
    if isinstance(expression, FunctionCall):
        return tuple(variable for item in expression.arguments for variable in expression_variables(item))
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        return expression_variables(expression.left) + expression_variables(expression.right)
    if isinstance(expression, InExpression):
        return expression_variables(expression.left) + expression_variables(expression.values)
    if isinstance(expression, NullPredicate):
        return expression_variables(expression.expression)
    if isinstance(expression, (NotExpression, UnaryExpression)):
        return expression_variables(expression.expression)
    if isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        return tuple(variable for item in expression.expressions for variable in expression_variables(item))
    if isinstance(expression, ListExpression):
        return tuple(variable for item in expression.items for variable in expression_variables(item))
    if isinstance(expression, MapExpression):
        return tuple(variable for _, item in expression.items for variable in expression_variables(item))
    if isinstance(expression, CaseExpression):
        parts = [expression.operand] if expression.operand is not None else []
        parts += [item for pair in expression.whens for item in pair]
        if expression.else_value is not None:
            parts.append(expression.else_value)
        return tuple(variable for item in parts for variable in expression_variables(item))
    if isinstance(expression, SubscriptExpression):
        return expression_variables(expression.target) + expression_variables(expression.index)
    if isinstance(expression, SliceExpression):
        parts = [expression.target]
        if expression.start is not None:
            parts.append(expression.start)
        if expression.end is not None:
            parts.append(expression.end)
        return tuple(variable for item in parts for variable in expression_variables(item))
    if isinstance(expression, ListComprehension):
        return _free_variables(
            (expression.iterable, expression.where, expression.projection),
            {expression.variable},
        )
    if isinstance(expression, ReduceExpression):
        return _free_variables(
            (expression.initial, expression.iterable, expression.expression),
            {expression.accumulator, expression.variable},
        )
    if isinstance(expression, MapProjectionExpression):
        parts: list[object] = [Variable(expression.variable)]
        parts += [item[2] for item in expression.items if item[0] == "alias"]
        return tuple(variable for item in parts for variable in expression_variables(item))
    if isinstance(expression, QuantifiedPredicate):
        return _free_variables(
            (expression.iterable, expression.where),
            {expression.variable},
        )
    if isinstance(expression, ExistsExpression):
        return expression_variables(expression.expression)
    if isinstance(expression, PropertyAccessExpression):
        return expression_variables(expression.expression)
    if isinstance(expression, list):
        return tuple(variable for item in expression for variable in expression_variables(item))
    if isinstance(expression, dict):
        return tuple(variable for item in expression.values() for variable in expression_variables(item))
    return ()


def _free_variables(parts: tuple[object | None, ...], bound: set[str]) -> tuple[str, ...]:
    """Return referenced variables excluding comprehension-local bindings."""
    return tuple(
        variable
        for item in parts
        if item is not None
        for variable in expression_variables(item)
        if variable not in bound
    )


def render_projection(expression: object) -> str:
    """Render a projection expression deterministically as a column name."""
    if expression == "*" or isinstance(expression, Wildcard):
        return "*"
    if isinstance(expression, Variable):
        return expression.name
    if isinstance(expression, PropertyRef):
        return f"{expression.variable}.{expression.property_name}"
    if isinstance(expression, Parameter):
        return f"${expression.name}"
    if expression is None:
        return "null"
    if isinstance(expression, bool):
        return "true" if expression else "false"
    if isinstance(expression, (int, float)):
        return str(expression)
    if isinstance(expression, str):
        return _render_string_literal(expression)
    if isinstance(expression, ArithmeticExpression):
        return f"({render_projection(expression.left)} {expression.operator} {render_projection(expression.right)})"
    if isinstance(expression, UnaryExpression):
        return f"({expression.operator}{render_projection(expression.expression)})"
    if isinstance(expression, ComparisonExpression):
        return f"({render_projection(expression.left)} {expression.operator} {render_projection(expression.right)})"
    if isinstance(expression, InExpression):
        return f"({render_projection(expression.left)} IN {render_projection(expression.values)})"
    if isinstance(expression, NullPredicate):
        rendered = render_projection(expression.expression)
        return f"({rendered} IS NOT NULL)" if expression.negated else f"({rendered} IS NULL)"
    if isinstance(expression, StringPredicate):
        return f"({render_projection(expression.left)} {expression.operator} {render_projection(expression.right)})"
    if isinstance(expression, NotExpression):
        return f"(NOT {render_projection(expression.expression)})"
    if isinstance(expression, AndExpression):
        return "(" + " AND ".join(render_projection(item) for item in expression.expressions) + ")"
    if isinstance(expression, OrExpression):
        return "(" + " OR ".join(render_projection(item) for item in expression.expressions) + ")"
    if isinstance(expression, XorExpression):
        return "(" + " XOR ".join(render_projection(item) for item in expression.expressions) + ")"
    if isinstance(expression, ListExpression):
        return "[" + ", ".join(render_projection(item) for item in expression.items) + "]"
    if isinstance(expression, MapExpression):
        return "{" + ", ".join(f"{key}: {render_projection(value)}" for key, value in expression.items) + "}"
    if isinstance(expression, CaseExpression):
        branches = " ".join(
            f"WHEN {render_projection(condition)} THEN {render_projection(value)}"
            for condition, value in expression.whens
        )
        operand = "" if expression.operand is None else f" {render_projection(expression.operand)}"
        else_branch = "" if expression.else_value is None else f" ELSE {render_projection(expression.else_value)}"
        return f"(CASE{operand} {branches}{else_branch})"
    if isinstance(expression, SubscriptExpression):
        return f"{render_projection(expression.target)}[{render_projection(expression.index)}]"
    if isinstance(expression, SliceExpression):
        start = "" if expression.start is None else render_projection(expression.start)
        end = "" if expression.end is None else render_projection(expression.end)
        return f"{render_projection(expression.target)}[{start}..{end}]"
    if isinstance(expression, ListComprehension):
        where = "" if expression.where is None else f" WHERE {render_projection(expression.where)}"
        projection = "" if expression.projection is None else f" | {render_projection(expression.projection)}"
        return f"[{expression.variable} IN {render_projection(expression.iterable)}{where}{projection}]"
    if isinstance(expression, ReduceExpression):
        return (
            f"reduce({expression.accumulator} = {render_projection(expression.initial)}, "
            f"{expression.variable} IN {render_projection(expression.iterable)} | "
            f"{render_projection(expression.expression)})"
        )
    if isinstance(expression, MapProjectionExpression):
        items = ", ".join(_render_map_projection_item(item) for item in expression.items)
        return f"{expression.variable}{{{items}}}"
    if isinstance(expression, QuantifiedPredicate):
        return (
            f"{expression.function}({expression.variable} IN "
            f"{render_projection(expression.iterable)} WHERE {render_projection(expression.where)})"
        )
    if isinstance(expression, ExistsExpression):
        return f"exists({render_projection(expression.expression)})"
    if isinstance(expression, PropertyAccessExpression):
        return f"{render_projection(expression.expression)}.{expression.property_name}"
    if isinstance(expression, list):
        return "[" + ", ".join(render_projection(item) for item in expression) + "]"
    if isinstance(expression, dict):
        return "{" + ", ".join(f"{key}: {render_projection(value)}" for key, value in expression.items()) + "}"
    if isinstance(expression, FunctionCall):
        rendered = ", ".join(render_projection(item) for item in expression.arguments)
        distinct = "DISTINCT " if expression.distinct else ""
        return f"{expression.name}({distinct}{rendered})"
    raise ValueError(f"Cannot render projection expression: {expression!r}")


def _render_map_projection_item(item: tuple) -> str:
    """Render one map-projection item deterministically."""
    if item[0] == "all":
        return ".*"
    if item[0] == "property":
        return f".{item[1]}"
    return f"{item[1]}: {render_projection(item[2])}"


def contains_function_call(expression: object) -> bool:
    """Return whether an expression contains any function call."""
    if isinstance(expression, FunctionCall):
        return True
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        return contains_function_call(expression.left) or contains_function_call(expression.right)
    if isinstance(expression, InExpression):
        return contains_function_call(expression.left) or contains_function_call(expression.values)
    if isinstance(expression, NullPredicate):
        return contains_function_call(expression.expression)
    if isinstance(expression, (NotExpression, UnaryExpression)):
        return contains_function_call(expression.expression)
    if isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        return any(contains_function_call(item) for item in expression.expressions)
    if isinstance(expression, ListExpression):
        return any(contains_function_call(item) for item in expression.items)
    if isinstance(expression, MapExpression):
        return any(contains_function_call(item) for _, item in expression.items)
    if isinstance(expression, CaseExpression):
        parts = [expression.operand] if expression.operand is not None else []
        parts += [item for pair in expression.whens for item in pair]
        if expression.else_value is not None:
            parts.append(expression.else_value)
        return any(contains_function_call(item) for item in parts)
    if isinstance(expression, SubscriptExpression):
        return contains_function_call(expression.target) or contains_function_call(expression.index)
    if isinstance(expression, SliceExpression):
        parts = [expression.target, expression.start, expression.end]
        return any(contains_function_call(item) for item in parts if item is not None)
    if isinstance(expression, ListComprehension):
        parts = [expression.iterable, expression.where, expression.projection]
        return any(contains_function_call(item) for item in parts if item is not None)
    if isinstance(expression, ReduceExpression):
        return any(
            contains_function_call(item)
            for item in (expression.initial, expression.iterable, expression.expression)
        )
    if isinstance(expression, MapProjectionExpression):
        return any(contains_function_call(item[2]) for item in expression.items if item[0] == "alias")
    if isinstance(expression, QuantifiedPredicate):
        return contains_function_call(expression.iterable) or contains_function_call(expression.where)
    if isinstance(expression, ExistsExpression):
        return contains_function_call(expression.expression)
    if isinstance(expression, PropertyAccessExpression):
        return contains_function_call(expression.expression)
    if isinstance(expression, list):
        return any(contains_function_call(item) for item in expression)
    if isinstance(expression, dict):
        return any(contains_function_call(item) for item in expression.values())
    return False


def contains_extended_expression(expression: object) -> bool:
    """Return whether an expression uses post-subset extended forms.

    The legacy ``parse()`` API cannot represent ``CASE``, subscripts, slices,
    comprehensions, ``reduce``, map projections, quantified predicates, or
    ``exists``; callers must use ``parse_ast()`` for those queries.
    """
    if isinstance(
        expression,
        (
            CaseExpression,
            SubscriptExpression,
            SliceExpression,
            ListComprehension,
            ReduceExpression,
            MapProjectionExpression,
            QuantifiedPredicate,
            ExistsExpression,
            PropertyAccessExpression,
        ),
    ):
        return True
    if isinstance(expression, FunctionCall):
        return any(contains_extended_expression(item) for item in expression.arguments)
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        return contains_extended_expression(expression.left) or contains_extended_expression(expression.right)
    if isinstance(expression, InExpression):
        return contains_extended_expression(expression.left) or contains_extended_expression(expression.values)
    if isinstance(expression, (NullPredicate, NotExpression, UnaryExpression)):
        return contains_extended_expression(expression.expression)
    if isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        return any(contains_extended_expression(item) for item in expression.expressions)
    if isinstance(expression, ListExpression):
        return any(contains_extended_expression(item) for item in expression.items)
    if isinstance(expression, MapExpression):
        return any(contains_extended_expression(item) for _, item in expression.items)
    if isinstance(expression, list):
        return any(contains_extended_expression(item) for item in expression)
    if isinstance(expression, dict):
        return any(contains_extended_expression(item) for item in expression.values())
    return False


def contains_aggregate_call(expression: object) -> bool:
    """Return whether an expression contains an aggregate function call."""
    if isinstance(expression, FunctionCall):
        if is_aggregate_function(expression.name):
            return True
        return any(contains_aggregate_call(item) for item in expression.arguments)
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        return contains_aggregate_call(expression.left) or contains_aggregate_call(expression.right)
    if isinstance(expression, InExpression):
        return contains_aggregate_call(expression.left) or contains_aggregate_call(expression.values)
    if isinstance(expression, (NullPredicate, NotExpression, UnaryExpression)):
        return contains_aggregate_call(expression.expression)
    if isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        return any(contains_aggregate_call(item) for item in expression.expressions)
    if isinstance(expression, (CaseExpression, SubscriptExpression)):
        return any(
            contains_aggregate_call(item)
            for item in _child_expressions(expression)
        )
    if isinstance(expression, (SliceExpression, ListComprehension, ReduceExpression)):
        return any(
            contains_aggregate_call(item)
            for item in _child_expressions(expression)
        )
    if isinstance(expression, (MapProjectionExpression, QuantifiedPredicate, ExistsExpression)):
        return any(
            contains_aggregate_call(item)
            for item in _child_expressions(expression)
        )
    if isinstance(expression, ListExpression):
        return any(contains_aggregate_call(item) for item in expression.items)
    if isinstance(expression, MapExpression):
        return any(contains_aggregate_call(item) for _, item in expression.items)
    if isinstance(expression, list):
        return any(contains_aggregate_call(item) for item in expression)
    if isinstance(expression, dict):
        return any(contains_aggregate_call(item) for item in expression.values())
    return False


def _child_expressions(expression: object) -> tuple[object, ...]:
    """Return the direct child expressions of an extended expression node."""
    if isinstance(expression, CaseExpression):
        parts = [expression.operand] if expression.operand is not None else []
        parts += [item for pair in expression.whens for item in pair]
        if expression.else_value is not None:
            parts.append(expression.else_value)
        return tuple(parts)
    if isinstance(expression, SubscriptExpression):
        return (expression.target, expression.index)
    if isinstance(expression, SliceExpression):
        return tuple(item for item in (expression.target, expression.start, expression.end) if item is not None)
    if isinstance(expression, ListComprehension):
        return tuple(item for item in (expression.iterable, expression.where, expression.projection) if item is not None)
    if isinstance(expression, ReduceExpression):
        return (expression.initial, expression.iterable, expression.expression)
    if isinstance(expression, MapProjectionExpression):
        return tuple(item[2] for item in expression.items if item[0] == "alias")
    if isinstance(expression, QuantifiedPredicate):
        return (expression.iterable, expression.where)
    if isinstance(expression, ExistsExpression):
        return (expression.expression,)
    if isinstance(expression, PropertyAccessExpression):
        return (expression.expression,)
    return ()


def validate_function_calls(expression: object, source: str, span: SourceSpan | None, *, allow_aggregate: bool) -> None:
    """Validate function names, arities, nesting, and aggregate placement."""
    calls = _iter_function_calls(expression)
    for call in calls:
        definition = get_function_def(call.name)
        if definition is None:
            _raise_semantic(f"Unsupported function: {call.name}", source, call.span or span)
        if definition.is_aggregate:
            if any(contains_function_call(argument) for argument in call.arguments):
                _raise_semantic("Nested aggregate calls are not supported", source, call.span or span)
            if len(call.arguments) != 1:
                _raise_semantic(f"{call.name} expects exactly one argument", source, call.span or span)
            argument = call.arguments[0]
            if isinstance(argument, Wildcard) and (call.name != "count" or call.distinct):
                _raise_semantic(f"{call.name}(*) is not supported", source, call.span or span)
        else:
            if call.distinct:
                _raise_semantic(
                    "DISTINCT is only supported for aggregate functions",
                    source,
                    call.span or span,
                )
            problem = check_scalar_arity(definition, len(call.arguments))
            if problem is not None:
                _raise_semantic(problem, source, call.span or span)
    if not allow_aggregate and contains_aggregate_call(expression):
        _raise_semantic("Aggregates cannot be used in WHERE", source, span)


def _iter_function_calls(expression: object) -> Iterator[FunctionCall]:
    """Yield function calls in an expression tree, outermost first."""
    if isinstance(expression, FunctionCall):
        yield expression
        return
    if isinstance(expression, (ComparisonExpression, ArithmeticExpression, StringPredicate)):
        yield from _iter_function_calls(expression.left)
        yield from _iter_function_calls(expression.right)
    elif isinstance(expression, InExpression):
        yield from _iter_function_calls(expression.left)
        yield from _iter_function_calls(expression.values)
    elif isinstance(expression, (NullPredicate, NotExpression, UnaryExpression)):
        yield from _iter_function_calls(expression.expression)
    elif isinstance(expression, (AndExpression, OrExpression, XorExpression)):
        for item in expression.expressions:
            yield from _iter_function_calls(item)
    elif isinstance(expression, ListExpression):
        for item in expression.items:
            yield from _iter_function_calls(item)
    elif isinstance(expression, MapExpression):
        for _, item in expression.items:
            yield from _iter_function_calls(item)
    elif isinstance(expression, CaseExpression):
        if expression.operand is not None:
            yield from _iter_function_calls(expression.operand)
        for condition, value in expression.whens:
            yield from _iter_function_calls(condition)
            yield from _iter_function_calls(value)
        if expression.else_value is not None:
            yield from _iter_function_calls(expression.else_value)
    elif isinstance(expression, SubscriptExpression):
        yield from _iter_function_calls(expression.target)
        yield from _iter_function_calls(expression.index)
    elif isinstance(expression, SliceExpression):
        yield from _iter_function_calls(expression.target)
        if expression.start is not None:
            yield from _iter_function_calls(expression.start)
        if expression.end is not None:
            yield from _iter_function_calls(expression.end)
    elif isinstance(expression, ListComprehension):
        yield from _iter_function_calls(expression.iterable)
        if expression.where is not None:
            yield from _iter_function_calls(expression.where)
        if expression.projection is not None:
            yield from _iter_function_calls(expression.projection)
    elif isinstance(expression, ReduceExpression):
        yield from _iter_function_calls(expression.initial)
        yield from _iter_function_calls(expression.iterable)
        yield from _iter_function_calls(expression.expression)
    elif isinstance(expression, MapProjectionExpression):
        for item in expression.items:
            if item[0] == "alias":
                yield from _iter_function_calls(item[2])
    elif isinstance(expression, QuantifiedPredicate):
        yield from _iter_function_calls(expression.iterable)
        yield from _iter_function_calls(expression.where)
    elif isinstance(expression, ExistsExpression):
        yield from _iter_function_calls(expression.expression)
    elif isinstance(expression, PropertyAccessExpression):
        yield from _iter_function_calls(expression.expression)
    elif isinstance(expression, list):
        for item in expression:
            yield from _iter_function_calls(item)
    elif isinstance(expression, dict):
        for item in expression.values():
            yield from _iter_function_calls(item)


def _render_string_literal(value: str) -> str:
    """Render a string literal with double quotes and minimal escaping."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _analyze_match(clause: MatchClause, scope: Scope, source: str) -> Scope:
    symbols = list(scope.symbols)
    by_name = {symbol.name: symbol for symbol in symbols}

    def introduce(name: str | None, kind: SymbolKind) -> None:
        if name is None:
            return
        previous = by_name.get(name)
        if previous is not None:
            if previous.kind != kind:
                _raise_semantic(
                    f"Variable {name} cannot be used as both a node and a relationship",
                    source,
                    clause.span,
                    name,
                )
            return
        symbol = Symbol(name, kind, clause.span)
        symbols.append(symbol)
        by_name[name] = symbol

    for pattern in clause.patterns:
        introduce(pattern.source.variable, SymbolKind.NODE)
        for hop in pattern.hops:
            introduce(hop.rel_var, SymbolKind.RELATIONSHIP)
            introduce(hop.target.variable, SymbolKind.NODE)
    return Scope(tuple(symbols))


def _resolve_projections(
    clause: ReturnClause | WithClause, scope: Scope, source: str, label: str = "RETURN"
) -> tuple[ResolvedProjection, ...]:
    if any(isinstance(item.expression, Wildcard) for item in clause.items):
        if len(clause.items) != 1 or not isinstance(clause.items[0].expression, Wildcard):
            _raise_semantic(f"{label} * must be the only projection item", source, clause.span)
        if not scope.symbols:
            _raise_semantic(f"{label} * requires bound variables", source, clause.span)
        wildcard = clause.items[0]
        return tuple(
            ResolvedProjection(
                wildcard,
                Variable(symbol.name),
                Symbol(symbol.name, symbol.kind, wildcard.span),
                symbol.name,
            )
            for symbol in scope.symbols
        )

    resolved: list[ResolvedProjection] = []
    names: set[str] = set()
    for item in clause.items:
        _validate_expression(item.expression, scope, source, label, item.span or clause.span)
        validate_function_calls(item.expression, source, item.span or clause.span, allow_aggregate=True)
        if contains_aggregate_call(item.expression) and not (
            isinstance(item.expression, FunctionCall) and is_aggregate_function(item.expression.name)
        ):
            _raise_semantic(
                "Aggregate calls must be top-level projection expressions",
                source,
                item.span or clause.span,
            )
        rendered = render_projection(item.expression)
        output_name = item.alias or rendered
        if output_name in names:
            _raise_semantic(f"{label} contains duplicate column names", source, item.span or clause.span)
        names.add(output_name)
        kind = _projection_kind(item.expression, scope)
        resolved.append(
            ResolvedProjection(item, item.expression, Symbol(output_name, kind, item.span), rendered)
        )
    return tuple(resolved)


def _projection_kind(expression: object, scope: Scope) -> SymbolKind:
    if isinstance(expression, Variable):
        symbol = scope.resolve(expression.name)
        if symbol is not None:
            return symbol.kind
    return SymbolKind.VALUE


def _validate_expression(expression: object, scope: Scope, source: str, clause: str, span: SourceSpan | None) -> None:
    for variable in expression_variables(expression):
        if scope.resolve(variable) is None:
            _raise_semantic(f"{clause} references unbound variable: {variable}", source, span, variable)


def _merge_scopes(first: Scope, second: Scope) -> Scope:
    symbols = list(first.symbols)
    names = set(first.names)
    for symbol in second.symbols:
        if symbol.name not in names:
            symbols.append(symbol)
            names.add(symbol.name)
    return Scope(tuple(symbols))


def _raise_semantic(message: str, source: str, span: SourceSpan | None, needle: str | None = None) -> None:
    start = span.start_offset if span is not None else 0
    end = span.end_offset if span is not None else len(source)
    offset = source.find(needle, start, end) if needle else start
    if offset < 0:
        offset = start
    line = source.count("\n", 0, offset) + 1
    line_start = source.rfind("\n", 0, offset) + 1
    raise CypherSemanticError(
        message,
        line=line,
        column=offset - line_start + 1,
        offset=offset,
        source=source,
    )
