"""Clause-aware semantic analysis for the canonical Cypher AST."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .cypher_ast import (
    AndExpression,
    ArithmeticExpression,
    ComparisonExpression,
    InExpression,
    ListExpression,
    MapExpression,
    MatchClause,
    NotExpression,
    NullPredicate,
    OrExpression,
    Parameter,
    ProjectionItem,
    PropertyRef,
    Query,
    ReturnClause,
    SourceSpan,
    StringPredicate,
    UnaryExpression,
    Variable,
    WhereClause,
    Wildcard,
    XorExpression,
)
from .cypher_errors import CypherSemanticError


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
        if isinstance(clause, MatchClause):
            scope = _analyze_match(clause, scope, query.source)
        elif isinstance(clause, WhereClause):
            if not isinstance(previous, MatchClause):
                _raise_semantic("WHERE must immediately follow MATCH or WITH", query.source, clause.span)
            _validate_expression(clause.expression, scope, query.source, "WHERE", clause.span)
        elif isinstance(clause, ReturnClause):
            projections = _resolve_projections(clause, scope, query.source)
            scope = Scope(tuple(projection.output for projection in projections))
            order_scope = _merge_scopes(incoming, scope)
            for item in clause.order_by:
                _validate_expression(item.expression_ast, order_scope, query.source, "ORDER BY", clause.span)
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
    if isinstance(expression, list):
        return tuple(variable for item in expression for variable in expression_variables(item))
    if isinstance(expression, dict):
        return tuple(variable for item in expression.values() for variable in expression_variables(item))
    return ()


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
    if isinstance(expression, list):
        return "[" + ", ".join(render_projection(item) for item in expression) + "]"
    if isinstance(expression, dict):
        return "{" + ", ".join(f"{key}: {render_projection(value)}" for key, value in expression.items()) + "}"
    raise ValueError(f"Cannot render projection expression: {expression!r}")


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


def _resolve_projections(clause: ReturnClause, scope: Scope, source: str) -> tuple[ResolvedProjection, ...]:
    if any(isinstance(item.expression, Wildcard) for item in clause.items):
        if len(clause.items) != 1 or not isinstance(clause.items[0].expression, Wildcard):
            _raise_semantic("RETURN * must be the only projection item", source, clause.span)
        if not scope.symbols:
            _raise_semantic("RETURN * requires bound variables", source, clause.span)
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
        _validate_expression(item.expression, scope, source, "RETURN", item.span or clause.span)
        rendered = render_projection(item.expression)
        output_name = item.alias or rendered
        if output_name in names:
            _raise_semantic("RETURN contains duplicate column names", source, item.span or clause.span)
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
