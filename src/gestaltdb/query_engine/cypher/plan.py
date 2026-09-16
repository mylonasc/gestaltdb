"""Logical planning for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace

from .ast import (
    CreateClause,
    DeleteClause,
    ForeachClause,
    FunctionCall,
    MatchClause,
    MergeClause,
    OrderItem,
    OptionalMatchClause,
    PathPatternClause,
    PathSelector,
    PropertyRef,
    Query,
    RemoveClause,
    ReturnClause,
    SampleTypedPathsCall,
    SetClause,
    SubqueryClause,
    UnionQuery,
    UnwindClause,
    Variable,
    WhereClause,
    Wildcard,
    WithClause,
)
from .errors import CypherSemanticError
from .functions import is_aggregate_function


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
    selector: PathSelector | None = None


@dataclass(frozen=True)
class OptionalMatchStep:
    """Match one textual ``OPTIONAL MATCH`` with left-outer-join semantics.

    ``where`` carries the immediately following ``WHERE`` expression, if any,
    for index-eligible seeks.     Unmatched input rows are preserved with newly
    introduced variables bound to ``None``.
    """

    patterns: tuple[PathPatternClause, ...]
    group_id: int
    where: object = None
    selector: PathSelector | None = None


@dataclass(frozen=True)
class Unwind:
    """Expand each input row into one row per element of a list expression."""

    expression: object
    variable: str


@dataclass(frozen=True)
class CallSubquery:
    """Execute an inner plan per input row and merge its records into bindings."""

    plan: LogicalPlan
    returns: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreateStep:
    """Create the nodes and edges of one textual ``CREATE`` per input row."""

    patterns: tuple[PathPatternClause, ...]


@dataclass(frozen=True)
class SetStep:
    """Apply one textual ``SET`` per input row with copy-on-write."""

    items: tuple[object, ...]


@dataclass(frozen=True)
class RemoveStep:
    """Apply one textual ``REMOVE`` per input row with copy-on-write."""

    items: tuple[object, ...]


@dataclass(frozen=True)
class DeleteStep:
    """Delete the entities of one textual ``DELETE`` per input row."""

    expressions: tuple[object, ...]
    detach: bool = False


@dataclass(frozen=True)
class MergeStep:
    """Match a pattern per input row or create it, running ``ON`` actions."""

    patterns: tuple[PathPatternClause, ...]
    on_create: tuple[object, ...] = ()
    on_match: tuple[object, ...] = ()


@dataclass(frozen=True)
class ForeachStep:
    """Run a write-only inner plan per input row and list element."""

    variable: str
    iterable: object
    plan: LogicalPlan


@dataclass(frozen=True)
class Union:
    """Combine independently planned branch queries with set semantics."""

    branches: tuple[LogicalPlan, ...]
    union_all: tuple[bool, ...]
    columns: tuple[str, ...] = ()


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
    expressions: tuple[object, ...] = ()


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


def plan_union_query(query: UnionQuery) -> LogicalPlan:
    """Plan each ``UNION`` branch independently and check column compatibility.

    Every branch must return exactly the same columns in the same order;
    branch ``ORDER BY``/``SKIP``/``LIMIT`` apply within their branch.
    """
    from .semantics import analyze_query

    branch_plans = tuple(plan_staged_query(branch) for branch in query.branches)
    columns = branch_plans[0].columns
    for branch_plan in branch_plans[1:]:
        if branch_plan.columns != columns:
            raise CypherSemanticError(
                f"UNION branches must return the same columns, got {columns} and {branch_plan.columns}",
                line=1,
                column=1,
                offset=0,
                source=query.source,
            )
    return LogicalPlan(
        (Union(branch_plans, query.union_all, columns),), None, columns, True
    )


def plan_staged_query(query: Query, scope=None, require_return: bool = True) -> LogicalPlan:
    """Plan a canonical query with ``WITH`` stages as executable operators.

    Operator order within each projection clause follows Cypher semantics:
    project, then ``DISTINCT``, then the ``WHERE`` filter owned by that
    stage, then ``ORDER BY``, ``SKIP``, and ``LIMIT``.

    ``scope`` seeds the initial scope so correlated subqueries resolve outer
    variables; top-level queries start empty. ``require_return`` is disabled
    for ``FOREACH`` bodies, which are write-only.
    """
    from .semantics import Scope, Symbol, SymbolKind, analyze_query

    analysis = analyze_query(query, scope, require_return) if scope is not None else analyze_query(query, require_return=require_return)
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
                operators.append(OptionalMatchStep(clause.patterns, group_id, attached_where, clause.selector))
            else:
                operators.append(MatchStep(clause.patterns, group_id, attached_where, clause.selector))
            group_id += 1
        elif isinstance(clause, UnwindClause):
            operators.append(Unwind(clause.expression, clause.variable))
        elif isinstance(clause, SubqueryClause):
            entry = by_clause[id(clause)]
            subquery_plan = plan_staged_query(clause.query, entry.scope_before)
            operators.append(CallSubquery(subquery_plan, subquery_plan.columns))
        elif isinstance(clause, CreateClause):
            operators.append(CreateStep(clause.patterns))
        elif isinstance(clause, MergeClause):
            operators.append(MergeStep(clause.patterns, clause.on_create, clause.on_match))
        elif isinstance(clause, ForeachClause):
            entry = by_clause[id(clause)]
            inner_scope = Scope(
                (*entry.scope_before.symbols, Symbol(clause.variable, SymbolKind.VALUE, clause.span))
            )
            inner_plan = plan_staged_query(Query(clause.body, query.source), inner_scope, False)
            operators.append(ForeachStep(clause.variable, clause.iterable, inner_plan))
        elif isinstance(clause, SetClause):
            operators.append(SetStep(clause.items))
        elif isinstance(clause, RemoveClause):
            operators.append(RemoveStep(clause.items))
        elif isinstance(clause, DeleteClause):
            operators.append(DeleteStep(clause.expressions, clause.detach))
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
    """Return whether any projection expression contains an aggregate call."""
    from .semantics import contains_aggregate_call

    return any(contains_aggregate_call(expression) for expression in expressions)


def _plan_aggregate(
    source: str,
    clause: WithClause | ReturnClause,
    outputs: tuple[str, ...],
    expressions: tuple[object, ...],
) -> Aggregate:
    """Build an ``Aggregate`` operator with implicit grouping keys.

    Mixed expressions are split into aggregate-free grouping slots and
    aggregate-call slots. The runtime evaluates the rewritten scalar
    expression once those slots have values for each group.
    """
    from .semantics import contains_aggregate_call, expression_variables

    keys: list[tuple[str, object]] = []
    calls: list[tuple[str, AggregateCall]] = []
    projected: list[object] = []
    slot_number = 0

    def slot_name(kind: str) -> str:
        nonlocal slot_number
        name = f"__gestaltdb_{kind}_{slot_number}"
        slot_number += 1
        return name

    def rewrite_expression(expression: object, output: str | None = None) -> object:
        if isinstance(expression, FunctionCall) and is_aggregate_function(expression.name):
            argument = expression.arguments[0]
            name = output or slot_name("aggregate")
            calls.append(
                (
                    name,
                    AggregateCall(
                        function=expression.name,
                        argument=None if isinstance(argument, Wildcard) else argument,
                        distinct=expression.distinct,
                    ),
                )
            )
            return Variable(name)
        if isinstance(expression, tuple):
            return tuple(rewrite_expression(item) for item in expression)
        if not contains_aggregate_call(expression):
            if expression_variables(expression):
                name = output or slot_name("group")
                keys.append((name, expression))
                return Variable(name)
            return expression
        if isinstance(expression, list):
            return [rewrite_expression(item) for item in expression]
        if isinstance(expression, dict):
            return {name: rewrite_expression(value) for name, value in expression.items()}
        if is_dataclass(expression):
            return replace(
                expression,
                **{
                    item.name: rewrite_expression(getattr(expression, item.name))
                    for item in fields(expression)
                    if item.name != "span"
                },
            )
        return expression

    for output, expression in zip(outputs, expressions):
        if not contains_aggregate_call(expression):
            keys.append((output, expression))
            projected.append(Variable(output))
        else:
            projected.append(rewrite_expression(expression, output))
    return Aggregate(
        returns=outputs,
        keys=tuple(keys),
        calls=tuple(calls),
        expressions=tuple(projected),
    )


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
