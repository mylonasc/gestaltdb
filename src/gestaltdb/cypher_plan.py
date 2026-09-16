"""Logical planning for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass

from .cypher_ast import (
    FunctionCall,
    MatchClause,
    OrderItem,
    OptionalMatchClause,
    PathPatternClause,
    PropertyRef,
    Query,
    ReturnClause,
    SampleTypedPathsCall,
    Variable,
    WhereClause,
    Wildcard,
    WithClause,
)
from .cypher_errors import CypherSemanticError
from .cypher_functions import is_aggregate_function


@dataclass(frozen=True)
class LogicalPlan:
    """Ordered logical operators for a parsed query."""

    operators: tuple[object, ...]
    source: object | None = None
    columns: tuple[str, ...] = ()
    staged: bool = False


@dataclass(frozen=True)
class MatchStep:
    """Match the patterns of one textual ``MATCH`` with a fresh scope.

    ``where`` optionally carries the ``WHERE`` expression that immediately
    follows the ``MATCH`` clause. The staged executor still applies it as a
    regular ``FilterExpression``; scan helpers may additionally use it to
    select index-backed candidate sets without changing results.
    """

    patterns: tuple[PathPatternClause, ...]
    group_id: int
    where: object = None


@dataclass(frozen=True)
class OptionalMatchStep:
    """Match one textual ``OPTIONAL MATCH`` with left-outer-join semantics.

    ``where`` carries the immediately following ``WHERE`` expression, if any,
    for index-eligible seeks. Unmatched input rows are preserved with newly
    introduced variables bound to ``None``.
    """

    patterns: tuple[PathPatternClause, ...]
    group_id: int
    where: object = None


@dataclass(frozen=True)
class ProjectItems:
    """Project one ``WITH`` or ``RETURN`` clause from resolved expressions."""

    returns: tuple[str, ...]
    expressions: tuple[object, ...] = ()


@dataclass(frozen=True)
class AggregateCall:
    """One aggregate invocation; a ``None`` argument counts every row."""

    function: str
    argument: object | None = None
    distinct: bool = False


@dataclass(frozen=True)
class Aggregate:
    """Group binding rows by key expressions and apply aggregate calls."""

    returns: tuple[str, ...]
    keys: tuple[tuple[str, object], ...] = ()
    calls: tuple[tuple[str, AggregateCall], ...] = ()


@dataclass(frozen=True)
class ProcedureSource:
    """Produce binding rows from a supported procedure call."""

    query: SampleTypedPathsCall


@dataclass(frozen=True)
class FilterExpression:
    """Filter rows by a boolean expression."""

    expression: object


@dataclass(frozen=True)
class Project:
    """Project result columns."""

    returns: tuple[str, ...]


@dataclass(frozen=True)
class Distinct:
    """Remove duplicate projected records."""


@dataclass(frozen=True)
class Sort:
    """Sort rows by the query's ORDER BY items."""

    items: tuple[object, ...]


@dataclass(frozen=True)
class Skip:
    """Skip result rows."""

    count: object


@dataclass(frozen=True)
class Limit:
    """Limit result rows."""

    limit: object


@dataclass(frozen=True)
class ProcedureCall:
    """Call a built-in procedure."""

    name: str


def plan_query(parsed: SampleTypedPathsCall) -> LogicalPlan:
    """Create a source-backed logical plan for a sampling procedure call."""
    operators = [ProcedureCall("pg.sample_typed_paths"), Project(parsed.returns)]
    _append_result_operators(operators, parsed)
    return LogicalPlan(
        tuple(operators), ProcedureSource(parsed), parsed.returns
    )


def plan_staged_query(query: Query) -> LogicalPlan:
    """Plan a canonical query with ``WITH`` stages as executable operators.

    Operator order within each projection clause follows Cypher semantics:
    project, then ``DISTINCT``, then the ``WHERE`` filter owned by that
    stage, then ``ORDER BY``, ``SKIP``, and ``LIMIT``.
    """
    from .cypher_semantics import analyze_query

    analysis = analyze_query(query)
    by_clause = {id(entry.clause): entry for entry in analysis.clauses}
    operators: list[object] = []
    group_id = 0
    index = 0
    clauses = query.clauses
    while index < len(clauses):
        clause = clauses[index]
        if isinstance(clause, (MatchClause, OptionalMatchClause)):
            attached_where = None
            if index + 1 < len(clauses) and isinstance(clauses[index + 1], WhereClause):
                attached_where = clauses[index + 1].expression
            if isinstance(clause, OptionalMatchClause):
                operators.append(OptionalMatchStep(clause.patterns, group_id, attached_where))
            else:
                operators.append(MatchStep(clause.patterns, group_id, attached_where))
            group_id += 1
        elif isinstance(clause, WhereClause):
            operators.append(FilterExpression(clause.expression))
        elif isinstance(clause, (WithClause, ReturnClause)):
            entry = by_clause[id(clause)]
            outputs = tuple(item.output.name for item in entry.projections)
            expressions = tuple(item.expression for item in entry.projections)
            if _has_aggregate_call(expressions):
                operators.append(_plan_aggregate(query.source, clause, outputs, expressions))
            else:
                operators.append(ProjectItems(returns=outputs, expressions=expressions))
            if clause.distinct:
                operators.append(Distinct())
            if index + 1 < len(clauses) and isinstance(clauses[index + 1], WhereClause):
                operators.append(FilterExpression(clauses[index + 1].expression))
                index += 1
            if clause.order_by:
                if _has_aggregate_call(expressions):
                    operators.append(
                        Sort(_normalize_aggregate_order(query.source, clause, outputs, expressions))
                    )
                else:
                    operators.append(Sort(clause.order_by))
            if clause.skip is not None:
                operators.append(Skip(clause.skip))
            if clause.limit is not None:
                operators.append(Limit(clause.limit))
        else:
            raise TypeError(f"unsupported canonical clause type: {type(clause).__name__}")
        index += 1
    return LogicalPlan(tuple(operators), None, analysis.output_names, True)


def _has_aggregate_call(expressions: tuple[object, ...]) -> bool:
    """Return whether any top-level projection expression is an aggregate call."""
    return any(
        isinstance(expression, FunctionCall) and is_aggregate_function(expression.name)
        for expression in expressions
    )


def _plan_aggregate(
    source: str,
    clause: WithClause | ReturnClause,
    outputs: tuple[str, ...],
    expressions: tuple[object, ...],
) -> Aggregate:
    """Build an ``Aggregate`` operator with implicit grouping keys."""
    keys: list[tuple[str, object]] = []
    calls: list[tuple[str, AggregateCall]] = []
    for output, expression in zip(outputs, expressions):
        if isinstance(expression, FunctionCall) and is_aggregate_function(expression.name):
            argument = expression.arguments[0]
            calls.append(
                (
                    output,
                    AggregateCall(
                        function=expression.name,
                        argument=None if isinstance(argument, Wildcard) else argument,
                        distinct=expression.distinct,
                    ),
                )
            )
        else:
            keys.append((output, expression))
    return Aggregate(returns=outputs, keys=tuple(keys), calls=tuple(calls))


def _normalize_aggregate_order(
    source: str,
    clause: WithClause | ReturnClause,
    outputs: tuple[str, ...],
    expressions: tuple[object, ...],
) -> tuple[OrderItem, ...]:
    """Rewrite aggregate ``ORDER BY`` items to direct output references.

    Post-aggregation rows only carry output values, so every order item must
    resolve to a projected output column.
    """
    normalized: list[OrderItem] = []
    for item in clause.order_by:
        expression = item.expression_ast
        if isinstance(expression, Variable) and expression.name in outputs:
            normalized.append(item)
            continue
        match = next(
            (output for output, projected in zip(outputs, expressions) if expression is not None and expression == projected),
            None,
        )
        if match is None and isinstance(expression, PropertyRef):
            rendered = f"{expression.variable}.{expression.property_name}"
            match = rendered if rendered in outputs else None
        if match is None:
            offset = clause.span.start_offset if clause.span is not None else 0
            line = source.count("\n", 0, offset) + 1
            line_start = source.rfind("\n", 0, offset) + 1
            raise CypherSemanticError(
                "ORDER BY in aggregate queries must reference projected outputs",
                line=line,
                column=offset - line_start + 1,
                offset=offset,
                source=source,
            )
        normalized.append(OrderItem(match, item.descending, Variable(match)))
    return tuple(normalized)


def _append_result_operators(operators: list[object], parsed) -> None:
    if getattr(parsed, "order_by", ()):
        operators.append(Sort(parsed.order_by))
    if getattr(parsed, "distinct", False):
        operators.append(Distinct())
    if getattr(parsed, "skip", None) is not None:
        operators.append(Skip(parsed.skip))
    if parsed.limit is not None:
        operators.append(Limit(parsed.limit))
