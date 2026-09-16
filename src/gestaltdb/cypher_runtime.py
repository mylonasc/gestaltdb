"""Streaming runtime operators for the GestaltDB Cypher subset."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Protocol

from .cypher_ast import (
    AndExpression,
    ComparisonExpression,
    NodeScanQuery,
    Parameter,
    PathPatternClause,
    PatternHop,
    PropertyRef,
    Variable,
)
from .cypher_expr import _cypher_equals, evaluate_expression, project_value
from .cypher_plan import Aggregate as LogicalAggregate
from .cypher_plan import (
    CallSubquery,
    LogicalPlan,
    MatchStep,
    OptionalMatchStep,
    ProcedureCall,
    ProcedureSource,
    ProjectItems,
    Union,
    Unwind,
)
from .cypher_plan import Distinct as LogicalDistinct
from .cypher_plan import FilterExpression as LogicalFilterExpression
from .cypher_plan import Limit as LogicalLimit
from .cypher_plan import Project as LogicalProject
from .cypher_plan import Skip as LogicalSkip
from .cypher_plan import Sort as LogicalSort


@dataclass
class QueryContext:
    """Runtime state shared by physical operators during one query."""

    graph: object
    parameters: dict[str, object] = field(default_factory=dict)
    node_cache: dict[bytes, object] = field(default_factory=dict)
    edge_cache: dict[bytes, object] = field(default_factory=dict)

    def node_key_to_bytes(self, node_key):
        return self.graph.node_key_to_bytes(node_key)

    def get_node(self, node_id: bytes):
        if node_id not in self.node_cache:
            self.node_cache[node_id] = self.graph.get_node(node_id)
        return self.node_cache[node_id]

    def get_edge(self, edge_id: bytes):
        if edge_id not in self.edge_cache:
            self.edge_cache[edge_id] = self.graph.get_edge(edge_id)
        return self.edge_cache[edge_id]

    def resolve(self, value):
        if isinstance(value, Parameter):
            if value.name not in self.parameters:
                raise ValueError(f"Missing Cypher parameter: ${value.name}")
            return self.parameters[value.name]
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.resolve(item) for item in value)
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        return value


@dataclass(frozen=True, slots=True)
class BindingRow:
    """Typed variable bindings and traversal state for one runtime row.

    Rows are plain immutable value objects threaded through the operator
    pipeline (see ``Operator``). Attribute access is the only supported
    interface: ``bindings`` maps variable names to entities, ``current_node_id``
    tracks the traversal cursor, and ``used_relationship_ids`` enforces
    per-``MATCH`` relationship isomorphism.
    """

    bindings: dict[str, object]
    current_node_id: bytes | None = None
    used_relationship_ids: frozenset[bytes] = frozenset()

    @classmethod
    def from_row(cls, row: BindingRow | Mapping[str, object]) -> BindingRow:
        """Return ``row`` as a typed binding row.

        Plain ``BindingRow`` instances pass through unchanged. Legacy
        ``{"bindings": ..., "current_node_id": ...}`` dictionaries are still
        adapted, but that path is deprecated and will be removed once all
        operators emit ``BindingRow`` directly (see ``[cypher-02]``).
        """
        if isinstance(row, cls):
            return row
        return cls(
            bindings=dict(row["bindings"]),  # type: ignore[arg-type]
            current_node_id=row.get("current_node_id"),  # type: ignore[arg-type]
            used_relationship_ids=frozenset(row.get("used_relationship_ids", ())),  # type: ignore[arg-type]
        )

    def with_bindings(
        self,
        bindings: dict[str, object],
        *,
        current_node_id: bytes | None = None,
        preserve_current_node: bool = True,
        used_relationship_ids: frozenset[bytes] | None = None,
    ) -> BindingRow:
        """Return a row with updated bindings and traversal state."""
        return BindingRow(
            bindings=bindings,
            current_node_id=(
                self.current_node_id if preserve_current_node else current_node_id
            ),
            used_relationship_ids=(
                self.used_relationship_ids
                if used_relationship_ids is None
                else used_relationship_ids
            ),
        )


@dataclass(frozen=True, slots=True)
class ProjectedRow:
    """Typed projected values with optional source bindings for ordering.

    ``values`` maps output column names to projected values. ``source_bindings``
    retains the pre-projection variable bindings so ``ORDER BY`` can reference
    hidden (non-projected) expressions.
    """

    values: dict[str, object]
    source_bindings: dict[str, object] | None = None


def bindings_of(row: BindingRow | ProjectedRow | Mapping[str, object]) -> dict[str, object]:
    """Return the variable-binding mapping carried by a pipeline row."""
    if isinstance(row, BindingRow):
        return row.bindings
    if isinstance(row, ProjectedRow):
        return row.values
    return BindingRow.from_row(row).bindings


class Operator(Protocol):
    """Contract for one stage of the executable operator pipeline.

    Every operator consumes an iterable of rows and lazily yields rows for the
    next stage. Binding stages yield ``BindingRow``; projection stages yield
    ``ProjectedRow``. The phase-specific protocols below document which row
    kind each operator consumes and produces.
    """

    def execute(self, rows: Iterable[object], context: QueryContext) -> Iterable[object]: ...


class BindingOperator(Operator, Protocol):
    """Contract for an operator that transforms binding rows."""

    def execute(self, rows: Iterable[BindingRow], context: QueryContext) -> Iterator[BindingRow]: ...


class ProjectionOperator(Operator, Protocol):
    """Contract for an operator that projects binding rows."""

    def execute(self, rows: Iterable[BindingRow], context: QueryContext) -> Iterator[ProjectedRow]: ...


class ResultOperator(Operator, Protocol):
    """Contract for an operator that transforms projected rows."""

    def execute(self, rows: Iterable[ProjectedRow], context: QueryContext) -> Iterable[ProjectedRow]: ...


@dataclass(frozen=True, slots=True)
class ProjectOperator:
    """Evaluate result projections while retaining source bindings for sorting."""

    returns: tuple[str, ...]
    projections: tuple[str, ...] = ()
    projection_expressions: tuple[object, ...] = ()

    def execute(
        self, rows: Iterable[BindingRow], context: QueryContext
    ) -> Iterator[ProjectedRow]:
        projection_items = self.projections or self.returns
        for source_row in rows:
            bindings = bindings_of(source_row)
            if self.projection_expressions:
                values = {
                    column: evaluate_expression(expression, bindings, context)
                    for column, expression in zip(
                        self.returns, self.projection_expressions
                    )
                }
            else:
                values = {
                    column: project_value(bindings, projection)
                    for column, projection in zip(self.returns, projection_items)
                }
            yield ProjectedRow(values=values, source_bindings=bindings)


@dataclass(frozen=True, slots=True)
class ProjectionView:
    """Minimal projection metadata for alias-aware ordering in staged plans."""

    returns: tuple[str, ...]
    projection_expressions: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class SortOperator:
    """Materialize and stably sort projected rows by Cypher order items."""

    items: tuple[object, ...]
    parsed: object

    def execute(
        self, rows: Iterable[ProjectedRow], context: QueryContext
    ) -> Iterator[ProjectedRow]:
        sorted_rows = list(rows)
        for item in reversed(self.items):
            sorted_rows.sort(
                key=lambda row, order_item=item: _sortable_value(
                    _order_value(
                        row.source_bindings or {}, order_item, self.parsed, context
                    )
                ),
                reverse=item.descending,
            )
        yield from sorted_rows


@dataclass(frozen=True, slots=True)
class DistinctOperator:
    """Stream the first projected row for each distinct result value."""

    columns: tuple[str, ...]

    def execute(
        self, rows: Iterable[ProjectedRow], context: QueryContext
    ) -> Iterator[ProjectedRow]:
        del context
        seen = set()
        for row in rows:
            key = tuple(cypher_value_key(row.values.get(column)) for column in self.columns)
            if key in seen:
                continue
            seen.add(key)
            yield row


@dataclass(frozen=True, slots=True)
class SkipOperator:
    """Skip a fixed number of projected rows without materializing them."""

    count: int

    def execute(
        self, rows: Iterable[ProjectedRow], context: QueryContext
    ) -> Iterable[ProjectedRow]:
        del context
        return islice(rows, self.count, None)


@dataclass(frozen=True, slots=True)
class LimitOperator:
    """Stop a projected row stream after a fixed number of rows."""

    count: int

    def execute(
        self, rows: Iterable[ProjectedRow], context: QueryContext
    ) -> Iterable[ProjectedRow]:
        del context
        return islice(rows, self.count)


def execute_plan(plan: LogicalPlan, context: QueryContext) -> list[dict[str, object]]:
    """Execute an authoritative logical plan against one query context.

    Staged clause pipelines run in ``_execute_staged``. Procedure-call plans
    seed binding rows from ``plan.source`` through their leading
    ``ProcedureCall`` operator and then run the same result-shaping operators.
    Every other operator executes directly: unknown operators raise
    ``TypeError`` instead of being silently skipped.
    """
    if len(plan.operators) == 1 and isinstance(plan.operators[0], Union):
        return _execute_union(plan.operators[0], context)
    if plan.staged or any(
        isinstance(operator, (MatchStep, OptionalMatchStep, Unwind, CallSubquery, ProjectItems, LogicalAggregate)) for operator in plan.operators
    ):
        return _execute_staged(plan, context)
    if not isinstance(plan.source, ProcedureSource):
        raise TypeError(f"Unsupported plan source: {type(plan.source).__name__}")
    stream: Iterable[BindingRow] | Iterable[ProjectedRow] | None = None
    projected = False
    for operator in plan.operators:
        if isinstance(operator, ProcedureCall):
            if stream is not None or projected:
                raise TypeError("ProcedureCall must seed the plan before projection")
            stream = _procedure_rows(plan.source, context)
        elif isinstance(operator, LogicalProject):
            if projected or stream is None:
                raise TypeError("Logical plan contains multiple projections")
            stream = ProjectOperator(returns=operator.returns).execute(stream, context)
            projected = True
        elif isinstance(operator, LogicalSort):
            _require_projected(projected, operator)
            stream = SortOperator(operator.items, ProjectionView(plan.columns)).execute(stream, context)
        elif isinstance(operator, LogicalDistinct):
            _require_projected(projected, operator)
            stream = DistinctOperator(plan.columns).execute(stream, context)
        elif isinstance(operator, LogicalSkip):
            _require_projected(projected, operator)
            count = _resolve_pagination(operator.count, context, "SKIP")
            stream = SkipOperator(count or 0).execute(stream, context)
        elif isinstance(operator, LogicalLimit):
            _require_projected(projected, operator)
            count = _resolve_pagination(operator.limit, context, "LIMIT")
            stream = LimitOperator(count or 0).execute(stream, context)
        else:
            raise TypeError(
                f"Unsupported logical operator: {type(operator).__name__}"
            )
    if not projected or stream is None:
        raise TypeError("Logical plan does not contain a projection")
    return [dict(row.values) for row in stream]


def _procedure_rows(source: ProcedureSource, context: QueryContext) -> Iterator[BindingRow]:
    """Seed binding rows from a sampling procedure call."""
    paths = context.graph.sample_typed_paths(
        source.query.seed_ids, source.query.pattern
    )
    return (BindingRow(bindings={"path": path}) for path in paths)


def _execute_union(union: Union, context: QueryContext) -> list[dict[str, object]]:
    """Execute ``UNION`` branches left to right with set semantics.

    ``UNION ALL`` steps concatenate; each ``UNION`` step deduplicates
    everything accumulated so far, preserving first-seen order.
    """
    accumulated = _execute_staged(union.branches[0], context)
    for position, branch in enumerate(union.branches[1:]):
        rows = _execute_staged(branch, context)
        if union.union_all[position]:
            accumulated.extend(rows)
        else:
            accumulated = _distinct_records(accumulated + rows, union.columns)
    return accumulated


def apply_match_step(
    rows: Iterable[BindingRow], step: MatchStep, context: QueryContext
) -> Iterable[BindingRow]:
    """Match one textual ``MATCH`` clause with a fresh isomorphism scope."""
    staged: Iterable[BindingRow] = _reset_used_relationships(rows)
    for pattern in step.patterns:
        staged = apply_path_pattern_clause(staged, pattern, context, step.where)
    return staged


def apply_optional_match_step(
    rows: Iterable[BindingRow], step: OptionalMatchStep, context: QueryContext
) -> Iterable[BindingRow]:
    """Match one textual ``OPTIONAL MATCH`` clause as a left-outer join.

    Input rows with at least one match expand normally; input rows with no
    match survive with newly introduced variables bound to ``None``.
    """
    for row in _reset_used_relationships(rows):
        row = BindingRow.from_row(row)
        staged: Iterable[BindingRow] = iter([row])
        for pattern in step.patterns:
            staged = apply_path_pattern_clause(staged, pattern, context, step.where)
        matched = False
        for extended in staged:
            matched = True
            yield extended
        if not matched:
            yield _null_fill_row(row, step.patterns)


def _null_fill_row(row: BindingRow, patterns) -> BindingRow:
    """Return ``row`` with pattern-introduced variables bound to ``None``."""
    bindings = dict(row.bindings)
    for pattern in patterns:
        for variable in _pattern_variables(pattern):
            if variable not in bindings:
                bindings[variable] = None
    return row.with_bindings(bindings)


def apply_unwind(rows: Iterable[BindingRow], unwind: Unwind, context: QueryContext) -> Iterable[BindingRow]:
    """Expand each row into one row per element of the ``UNWIND`` list.

    ``None`` and empty lists produce no rows; non-list values raise
    ``TypeError``.
    """
    for row in rows:
        row = BindingRow.from_row(row)
        items = evaluate_expression(unwind.expression, row.bindings, context)
        if items is None:
            continue
        if not isinstance(items, (list, tuple)):
            raise TypeError("UNWIND expects a list value")
        for item in items:
            bindings = dict(row.bindings)
            bindings[unwind.variable] = item
            yield row.with_bindings(bindings)


def apply_call_subquery(
    rows: Iterable[BindingRow], call: CallSubquery, context: QueryContext
) -> Iterable[BindingRow]:
    """Execute an inner plan per input row and merge its records into bindings.

    Outer rows with an empty subquery result are dropped (correlated join).
    Returned values overwrite same-named outer bindings.
    """
    for row in rows:
        row = BindingRow.from_row(row)
        seed = row.with_bindings(
            dict(row.bindings),
            preserve_current_node=False,
            current_node_id=None,
            used_relationship_ids=frozenset(),
        )
        for record in _execute_staged(call.plan, context, initial=[seed]):
            merged = dict(seed.bindings)
            merged.update(record)
            yield seed.with_bindings(
                merged,
                preserve_current_node=False,
                current_node_id=None,
                used_relationship_ids=frozenset(),
            )


def _pattern_variables(pattern: PathPatternClause) -> tuple[str, ...]:
    """Return variable names introduced by one path pattern."""
    variables = []
    if pattern.source.variable is not None:
        variables.append(pattern.source.variable)
    for hop in pattern.hops:
        if hop.rel_var is not None:
            variables.append(hop.rel_var)
        if hop.target.variable is not None:
            variables.append(hop.target.variable)
    return tuple(variables)


@dataclass(frozen=True, slots=True)
class AggregateOperator:
    """Group rows by key expressions and apply the core six aggregates."""

    aggregate: LogicalAggregate

    def execute(
        self, rows: Iterable[BindingRow], context: QueryContext
    ) -> Iterator[ProjectedRow]:
        groups: dict[tuple[object, ...], dict[str, object]] = {}
        order: list[tuple[object, ...]] = []
        for source_row in rows:
            bindings = bindings_of(source_row)
            key_values = [
                evaluate_expression(expression, bindings, context)
                for _, expression in self.aggregate.keys
            ]
            key = tuple(cypher_value_key(value) for value in key_values)
            group = groups.get(key)
            if group is None:
                group = {"key_values": key_values, "row_count": 0, "arguments": []}
                groups[key] = group
                order.append(key)
            group["row_count"] += 1
            group["arguments"].append(
                [
                    evaluate_expression(call.argument, bindings, context)
                    if call.argument is not None
                    else None
                    for _, call in self.aggregate.calls
                ]
            )
        if not order and not self.aggregate.keys:
            empty = {"key_values": [], "row_count": 0, "arguments": []}
            groups[()] = empty
            order.append(())
        for key in order:
            group = groups[key]
            record: dict[str, object] = {
                output: value
                for (output, _), value in zip(self.aggregate.keys, group["key_values"])
            }
            columns = list(zip(*group["arguments"])) if group["arguments"] else [() for _ in self.aggregate.calls]
            for (output, call), values in zip(self.aggregate.calls, columns):
                record[output] = _apply_aggregate(call, list(values), group["row_count"])
            yield ProjectedRow(values=record, source_bindings=dict(record))


def _apply_aggregate(call, values: list[object], row_count: int) -> object:
    """Apply one aggregate call to its collected per-row argument values."""
    if call.function == "count" and call.argument is None:
        return row_count
    non_null = [value for value in values if value is not None]
    if call.distinct:
        seen: set[object] = set()
        deduplicated: list[object] = []
        for value in non_null:
            key = cypher_value_key(value)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(value)
        non_null = deduplicated
    if call.function == "count":
        return len(non_null)
    if call.function == "collect":
        return non_null
    if call.function in ("sum", "avg"):
        for value in non_null:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{call.function} expects numeric operands")
        if not non_null:
            return 0 if call.function == "sum" else None
        total = sum(non_null)
        return total if call.function == "sum" else total / len(non_null)
    if call.function in ("min", "max"):
        if not non_null:
            return None
        try:
            return min(non_null) if call.function == "min" else max(non_null)
        except TypeError as exc:
            raise TypeError(f"{call.function} expects comparable values") from exc
    raise TypeError(f"Unsupported aggregate function: {call.function}")


def filter_projected(
    rows: Iterable[ProjectedRow], expression: object, context: QueryContext
) -> Iterator[ProjectedRow]:
    """Yield projected rows whose output values satisfy a ``WHERE`` filter."""
    for row in rows:
        values = bindings_of(row)
        if evaluate_expression(expression, values, context) is True:
            yield row


def _execute_staged(
    plan: LogicalPlan, context: QueryContext, initial: Iterable[BindingRow] | None = None
) -> list[dict[str, object]]:
    """Execute a multi-stage ``WITH`` plan, threading scopes between stages.

    ``initial`` seeds the binding stream for correlated execution (such as
    ``CALL`` subqueries); top-level queries start from one empty row.
    """
    bindings: Iterable[BindingRow] | None = iter(initial) if initial is not None else iter([BindingRow(bindings={})])
    projected: Iterable[ProjectedRow] | None = None
    view = ProjectionView(())
    for operator in plan.operators:
        if isinstance(operator, (MatchStep, OptionalMatchStep)):
            if projected is not None:
                bindings = (
                    BindingRow(bindings=dict(row.values), current_node_id=None)
                    for row in projected
                )
                projected = None
            if isinstance(operator, OptionalMatchStep):
                bindings = apply_optional_match_step(bindings if bindings is not None else iter(()), operator, context)
            else:
                bindings = apply_match_step(bindings if bindings is not None else iter(()), operator, context)
        elif isinstance(operator, (Unwind, CallSubquery)):
            if projected is not None:
                bindings = (
                    BindingRow(bindings=dict(row.values), current_node_id=None)
                    for row in projected
                )
                projected = None
            if isinstance(operator, CallSubquery):
                bindings = apply_call_subquery(bindings if bindings is not None else iter(()), operator, context)
            else:
                bindings = apply_unwind(bindings if bindings is not None else iter(()), operator, context)
        elif isinstance(operator, LogicalFilterExpression):
            if projected is not None:
                projected = filter_projected(projected, operator.expression, context)
            else:
                bindings = filter_expression(bindings if bindings is not None else iter(()), operator.expression, context)
        elif isinstance(operator, ProjectItems):
            source: Iterable[BindingRow] | Iterable[ProjectedRow] = (
                projected if projected is not None else (bindings if bindings is not None else iter(()))
            )
            view = ProjectionView(operator.returns, operator.expressions)
            projected = ProjectOperator(
                returns=operator.returns,
                projection_expressions=operator.expressions,
            ).execute(source, context)
            bindings = None
        elif isinstance(operator, LogicalAggregate):
            aggregate_source: Iterable[BindingRow] | Iterable[ProjectedRow] = (
                projected if projected is not None else (bindings if bindings is not None else iter(()))
            )
            view = ProjectionView(
                operator.returns, tuple(Variable(name) for name in operator.returns)
            )
            projected = AggregateOperator(operator).execute(aggregate_source, context)
            bindings = None
        elif isinstance(operator, LogicalSort):
            if projected is None:
                raise TypeError("Sort cannot execute before projection")
            projected = SortOperator(operator.items, view).execute(projected, context)
        elif isinstance(operator, LogicalDistinct):
            if projected is None:
                raise TypeError("Distinct cannot execute before projection")
            projected = DistinctOperator(view.returns).execute(projected, context)
        elif isinstance(operator, LogicalSkip):
            if projected is None:
                raise TypeError("Skip cannot execute before projection")
            count = _resolve_pagination(operator.count, context, "SKIP")
            projected = SkipOperator(count or 0).execute(projected, context)
        elif isinstance(operator, LogicalLimit):
            if projected is None:
                raise TypeError("Limit cannot execute before projection")
            count = _resolve_pagination(operator.limit, context, "LIMIT")
            projected = LimitOperator(count or 0).execute(projected, context)
        else:
            raise TypeError(
                f"Unsupported staged operator: {type(operator).__name__}"
            )
    if projected is None:
        raise TypeError("Staged plan does not contain a projection")
    return [dict(row.values) for row in projected]


def _require_projected(projected: bool, operator: object) -> None:
    if not projected:
        raise TypeError(
            f"{type(operator).__name__} cannot execute before projection"
        )


def _reset_used_relationships(rows):
    for row in rows:
        typed_row = BindingRow.from_row(row)
        yield typed_row.with_bindings(dict(typed_row.bindings), used_relationship_ids=frozenset())


def node_scan_ids(parsed: NodeScanQuery, context: QueryContext):
    """Yield node IDs for a label scan, using property indexes when available."""
    if parsed.limit == 0:
        return iter(())
    label_ids = _node_ids_for_labels(parsed, context)
    range_scan = _node_range_scan(parsed, context)
    if range_scan is not None and parsed.property_name is None:
        return range_scan
    if parsed.property_name is None or parsed.property_name not in context.graph.indexed_node_properties:
        return label_ids

    property_value = context.resolve(parsed.property_value)
    labels = tuple(label for label in (parsed.labels or (parsed.label,)) if label is not None)
    if labels and hasattr(context.graph, "iter_node_ids_by_label_property"):
        node_ids = set(context.graph.iter_node_ids_by_label_property(labels[0], parsed.property_name, property_value))
        for label in labels[1:]:
            node_ids = node_ids.intersection(context.graph.iter_node_ids_by_label(label))
        return iter(sorted(node_ids))
    property_ids = set(context.graph.iter_node_ids_by_property(parsed.property_name, property_value))
    return iter(sorted(set(label_ids).intersection(property_ids)))


def _node_range_scan(parsed: NodeScanQuery, context: QueryContext):
    bounds = _range_bounds_for_node_scan(parsed, context)
    if bounds is None:
        return None
    property_name, start_value, end_value, include_start, include_end = bounds
    labels = tuple(label for label in (parsed.labels or (parsed.label,)) if label is not None)
    if labels and hasattr(context.graph, "iter_node_ids_by_label_property_range"):
        node_ids = set(context.graph.iter_node_ids_by_label_property_range(labels[0], property_name, start_value, end_value, include_start, include_end))
        for label in labels[1:]:
            node_ids = node_ids.intersection(context.graph.iter_node_ids_by_label(label))
        return iter(sorted(node_ids))
    if hasattr(context.graph, "iter_node_ids_by_property_range"):
        return context.graph.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)
    return None


def _range_bounds_for_node_scan(parsed: NodeScanQuery, context: QueryContext):
    if parsed.where is None:
        return None
    expressions = parsed.where.expressions if isinstance(parsed.where, AndExpression) else (parsed.where,)
    property_name = None
    start_value = None
    end_value = None
    include_start = True
    include_end = True
    found = False
    for expression in expressions:
        if not isinstance(expression, ComparisonExpression):
            continue
        if not isinstance(expression.left, PropertyRef):
            continue
        if not _is_seek_value(expression.right):
            continue
        if expression.left.variable != parsed.variable:
            continue
        if expression.operator not in {"<", "<=", ">", ">="}:
            continue
        if expression.left.property_name not in context.graph.indexed_node_properties:
            continue
        value = context.resolve(expression.right)
        current_property = expression.left.property_name
        if property_name is not None and property_name != current_property:
            continue
        property_name = current_property
        found = True
        if expression.operator in {">", ">="}:
            start_value = value
            include_start = expression.operator == ">="
        else:
            end_value = value
            include_end = expression.operator == "<="
    if not found:
        return None
    return property_name, start_value, end_value, include_start, include_end


def _node_ids_for_labels(parsed: NodeScanQuery, context: QueryContext):
    labels = parsed.labels or (parsed.label,)
    labels = tuple(label for label in labels if label is not None)
    if not labels:
        return context.graph.get_node_keys_generator()
    if len(labels) == 1:
        return context.graph.iter_node_ids_by_label(labels[0])
    matching_ids = None
    for label in labels:
        current_ids = set(context.graph.iter_node_ids_by_label(label))
        matching_ids = current_ids if matching_ids is None else matching_ids.intersection(current_ids)
    return iter(sorted(matching_ids or set()))


def apply_path_pattern_clause(rows, clause: PathPatternClause, context: QueryContext, where=None):
    """Apply a generalized fixed-length path pattern to incoming rows.

    When the clause-local ``WHERE`` holds an index-eligible predicate on the
    first relationship, the first hop is served from the composite
    type/property edge index instead of scanning node adjacency. The trailing
    ``FilterExpression`` operator always re-applies the full predicate, so
    index selection never changes results.
    """
    for row in rows:
        row = BindingRow.from_row(row)
        indexed = _indexed_first_hop_rows(row, clause, context, where)
        if indexed is None:
            expanded = _path_start_rows(row, clause, context, where)
            remaining = clause.hops
        else:
            expanded = indexed
            remaining = clause.hops[1:]
        for hop in remaining:
            expanded = _expand_pattern_hop(context, expanded, hop)
        yield from expanded


def _indexed_first_hop_rows(row, clause: PathPatternClause, context: QueryContext, where):
    """Yield first-hop rows from an edge property index, or ``None``.

    Returns ``None`` when the pattern cannot use the composite type/property
    edge index (bound source, anonymous or untyped relationship, or no
    eligible predicate), in which case the caller falls back to adjacency
    expansion.
    """
    if where is None or not clause.hops:
        return None
    hop = clause.hops[0]
    source = clause.source
    if hop.rel_var is None or not hop.edge_types:
        return None
    if source.variable is not None and source.variable in row.bindings:
        return None
    if any(name == "id" for name, _ in source.properties):
        return None
    spec = _edge_seek_spec(hop.rel_var, where, context)
    if spec is None:
        return None
    return _iter_indexed_first_hop(row, clause, hop, context, spec)


def _iter_indexed_first_hop(row, clause: PathPatternClause, hop: PatternHop, context: QueryContext, spec):
    """Iterate index-backed candidate edges for the first pattern hop."""
    seen = set()
    for edge_type in hop.edge_types:
        if spec[0] == "exact":
            _, property_name, value = spec
            edge_ids = context.graph.iter_edge_ids_by_type_property(edge_type, property_name, value)
        else:
            _, property_name, start_value, end_value, include_start, include_end = spec
            edge_ids = context.graph.iter_edge_ids_by_type_property_range(
                edge_type, property_name, start_value, end_value, include_start, include_end
            )
        for edge_id in edge_ids:
            if edge_id in seen or edge_id in row.used_relationship_ids:
                continue
            seen.add(edge_id)
            yield from _hydrate_indexed_edge(row, clause, hop, context, edge_id)


def _hydrate_indexed_edge(row, clause: PathPatternClause, hop: PatternHop, context: QueryContext, edge_id: bytes):
    """Bind one indexed edge to its endpoint nodes following hop direction."""
    edge = context.get_edge(edge_id)
    if edge is None:
        return
    source_id = context.node_key_to_bytes(edge.source)
    target_id = context.node_key_to_bytes(edge.target)
    source_node = context.get_node(source_id)
    target_node = context.get_node(target_id)
    if source_node is None or target_node is None:
        return
    if hop.direction == "in":
        orientations = [(target_node, source_node, target_id, source_id)]
    else:
        orientations = [(source_node, target_node, source_id, target_id)]
        if hop.direction == "any" and source_id != target_id:
            orientations.append((target_node, source_node, target_id, source_id))
    for start_node, neighbor_node, _, neighbor_id in orientations:
        if _is_null_bound(row, hop.target.variable) or _is_null_bound(row, hop.rel_var):
            continue
        if not _node_matches_pattern(start_node, clause.source.labels, clause.source.properties, context):
            continue
        if not _node_matches_pattern(neighbor_node, hop.target.labels, hop.target.properties, context):
            continue
        bindings = dict(row.bindings)
        if clause.source.variable is not None:
            bound_source = bindings.get(clause.source.variable)
            if bound_source is not None and not same_entity(bound_source, start_node):
                continue
            bindings[clause.source.variable] = start_node
        if hop.target.variable is not None:
            bound_target = bindings.get(hop.target.variable)
            if bound_target is not None and not same_entity(bound_target, neighbor_node):
                continue
            bindings[hop.target.variable] = neighbor_node
        bound_edge = bindings.get(hop.rel_var)
        if bound_edge is not None and not same_entity(bound_edge, edge):
            continue
        bindings[hop.rel_var] = edge
        yield row.with_bindings(
            bindings,
            current_node_id=neighbor_id,
            preserve_current_node=False,
            used_relationship_ids=row.used_relationship_ids.union((edge_id,)),
        )


def _edge_seek_spec(rel_var: str, where, context: QueryContext):
    """Return an edge property index scan spec for one relationship variable.

    Only ``AND``-ed exact (``=``) or single-property range predicates against
    indexed edge properties qualify. Returns ``None`` when no predicate can
    use the composite type/property index.
    """
    indexed = getattr(context.graph, "indexed_edge_properties", set())
    expressions = where.expressions if isinstance(where, AndExpression) else (where,)
    for expression in expressions:
        if (
            isinstance(expression, ComparisonExpression)
            and isinstance(expression.left, PropertyRef)
            and expression.left.variable == rel_var
            and expression.operator == "="
            and _is_seek_value(expression.right)
            and expression.left.property_name in indexed
            and hasattr(context.graph, "iter_edge_ids_by_type_property")
        ):
            return ("exact", expression.left.property_name, context.resolve(expression.right))
    property_name = None
    start_value = None
    end_value = None
    include_start = True
    include_end = True
    found = False
    for expression in expressions:
        if not isinstance(expression, ComparisonExpression):
            continue
        if not isinstance(expression.left, PropertyRef):
            continue
        if not _is_seek_value(expression.right):
            continue
        if expression.left.variable != rel_var:
            continue
        if expression.operator not in {"<", "<=", ">", ">="}:
            continue
        if expression.left.property_name not in indexed:
            continue
        current_property = expression.left.property_name
        if property_name is not None and property_name != current_property:
            continue
        property_name = current_property
        found = True
        value = context.resolve(expression.right)
        if expression.operator in {">", ">="}:
            start_value = value
            include_start = expression.operator == ">="
        else:
            end_value = value
            include_end = expression.operator == "<="
    if not found or not hasattr(context.graph, "iter_edge_ids_by_type_property_range"):
        return None
    return ("range", property_name, start_value, end_value, include_start, include_end)


def _is_seek_value(value) -> bool:
    return value is None or isinstance(value, (Parameter, str, int, float, bool, list, dict))


def _path_start_rows(row, clause: PathPatternClause, context: QueryContext, where=None):
    source = clause.source
    identity = next((value for name, value in source.properties if name == "id"), None)
    properties = tuple(item for item in source.properties if item[0] != "id") if identity is not None else source.properties
    bound_node = row.bindings.get(source.variable) if source.variable is not None else None
    if (
        source.variable is not None
        and source.variable in row.bindings
        and row.bindings[source.variable] is None
    ):
        return
    if bound_node is not None:
        identity_matches = identity is None or _cypher_equals(bound_node.get_id, context.resolve(identity)) is True
        if _is_node(bound_node) and identity_matches and _node_matches_pattern(bound_node, source.labels, properties, context):
            yield row.with_bindings(
                dict(row.bindings),
                current_node_id=context.node_key_to_bytes(bound_node.get_id),
                preserve_current_node=False,
            )
        return

    if identity is not None:
        node_id = context.node_key_to_bytes(context.resolve(identity))
        node = context.get_node(node_id)
        if node is not None and _node_matches_pattern(node, source.labels, properties, context):
            bindings = dict(row.bindings)
            if source.variable is not None:
                bindings[source.variable] = node
            yield row.with_bindings(bindings, current_node_id=node_id, preserve_current_node=False)
        return

    first_name, first_value = properties[0] if properties else (None, None)
    scan = NodeScanQuery(
        variable=source.variable or "",
        label=source.labels[0] if source.labels else None,
        property_name=first_name,
        property_value=first_value,
        returns=(),
        labels=source.labels,
        properties=properties,
        where=where,
    )
    for node_id in node_scan_ids(scan, context):
        node = context.get_node(node_id)
        if node is None or not _node_matches_pattern(node, source.labels, properties, context):
            continue
        bindings = dict(row.bindings)
        if source.variable is not None:
            bindings[source.variable] = node
        yield row.with_bindings(bindings, current_node_id=node_id, preserve_current_node=False)


def _expand_pattern_hop(context: QueryContext, rows, hop: PatternHop):
    for row in rows:
        row = BindingRow.from_row(row)
        if _is_null_bound(row, hop.target.variable) or _is_null_bound(row, hop.rel_var):
            continue
        seen = set()
        for adjacency in _iter_pattern_adjacency(context, row.current_node_id, hop):
            edge_id = adjacency["edge_id"]
            occurrence = (edge_id, adjacency["neighbor_id"])
            if occurrence in seen or edge_id in row.used_relationship_ids:
                continue
            seen.add(occurrence)
            target_node = context.get_node(adjacency["neighbor_id"])
            if target_node is None or not _node_matches_pattern(
                target_node, hop.target.labels, hop.target.properties, context
            ):
                continue
            bindings = dict(row.bindings)
            if hop.target.variable is not None:
                bound_target = bindings.get(hop.target.variable)
                if bound_target is not None and not same_entity(bound_target, target_node):
                    continue
                bindings[hop.target.variable] = target_node
            if hop.rel_var is not None:
                edge = context.get_edge(edge_id)
                if edge is None:
                    continue
                bound_edge = bindings.get(hop.rel_var)
                if bound_edge is not None and not same_entity(bound_edge, edge):
                    continue
                bindings[hop.rel_var] = edge
            yield row.with_bindings(
                bindings,
                current_node_id=adjacency["neighbor_id"],
                preserve_current_node=False,
                used_relationship_ids=row.used_relationship_ids.union((edge_id,)),
            )


def _is_null_bound(row: BindingRow, variable: str | None) -> bool:
    """Return whether a variable is bound to ``None`` (e.g. by ``OPTIONAL MATCH``).

    ``None``-bound variables never match further patterns, but rows carrying
    them survive cartesian products and projections.
    """
    return variable is not None and variable in row.bindings and row.bindings[variable] is None


def _iter_pattern_adjacency(context: QueryContext, node_id: bytes, hop: PatternHop):
    if hop.edge_types:
        for edge_type in hop.edge_types:
            yield from context.graph.iter_typed_adjacency(node_id, edge_type, direction=hop.direction)
        return
    for edge_id in context.graph.iter_edge_ids():
        edge = context.get_edge(edge_id)
        if edge is None:
            continue
        source_id = context.node_key_to_bytes(edge.source)
        target_id = context.node_key_to_bytes(edge.target)
        if hop.direction in {"out", "any"} and source_id == node_id:
            yield {"edge_id": edge_id, "neighbor_id": target_id}
        if (
            hop.direction == "in" and target_id == node_id
            or hop.direction == "any" and target_id == node_id and source_id != target_id
        ):
            yield {"edge_id": edge_id, "neighbor_id": source_id}


def _node_matches_pattern(node, labels, properties, context: QueryContext) -> bool:
    if labels and not set(labels).issubset(set(getattr(node, "labels", ()))):
        return False
    for property_name, property_value in properties:
        if _cypher_equals(node.properties.get(property_name), context.resolve(property_value)) is not True:
            return False
    return True


def _is_node(value) -> bool:
    return hasattr(value, "labels") and hasattr(value, "properties") and hasattr(value, "get_id")


def filter_expression(rows, expression, context: QueryContext):
    """Yield rows that satisfy a supported boolean expression."""
    for row in rows:
        row = BindingRow.from_row(row)
        if evaluate_expression(expression, row.bindings, context) is True:
            yield row


def _order_value(bindings, order_item, parsed, context):
    expression = getattr(order_item, "expression_ast", None)
    alias = None
    if isinstance(expression, Variable) and expression.name in parsed.returns:
        alias = expression.name
    elif isinstance(expression, PropertyRef) and expression.variable in parsed.returns:
        alias = expression.variable
    if alias is not None:
        index = parsed.returns.index(alias)
        projection_expressions = getattr(parsed, "projection_expressions", ())
        if projection_expressions:
            value = evaluate_expression(projection_expressions[index], bindings, context)
            if isinstance(expression, PropertyRef):
                return project_value({alias: value}, f"{alias}.{expression.property_name}")
            return value
    if expression is not None:
        return evaluate_expression(expression, bindings, context)
    return project_value(bindings, order_item.expression)


def _resolve_pagination(value, context: QueryContext, clause: str) -> int | None:
    if value is None:
        return None
    resolved = context.resolve(value)
    if isinstance(resolved, bool) or not isinstance(resolved, int) or resolved < 0:
        raise ValueError(f"{clause} must be a non-negative integer")
    return resolved


def _sortable_value(value):
    return (value is None, value)


def cypher_value_key(value):
    """Return a hashable identity key for grouping and deduplication.

    Booleans are tagged separately from numbers so ``True`` and ``1`` stay
    distinct, and graph entities key by kind plus stable ID instead of object
    identity.
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, (list, tuple)):
        return ("list", tuple(cypher_value_key(item) for item in value))
    if isinstance(value, dict):
        return (
            "map",
            tuple(sorted((key, cypher_value_key(item)) for key, item in value.items())),
        )
    if hasattr(value, "get_id") and hasattr(value, "properties"):
        entity_id = value.get_id() if callable(value.get_id) else value.get_id
        if hasattr(value, "labels"):
            return ("node", entity_id)
        return ("edge", entity_id)
    try:
        hash(value)
    except TypeError:
        return ("repr", repr(value))
    return ("value", value)


def _distinct_records(records: list[dict[str, object]], columns: tuple[str, ...]) -> list[dict[str, object]]:
    seen = set()
    distinct = []
    for record in records:
        key = tuple(cypher_value_key(record.get(column)) for column in columns)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(record)
    return distinct


def same_entity(left, right) -> bool:
    """Return whether two graph entities represent the same stored entity."""
    left_id = getattr(left, "get_id", None)
    right_id = getattr(right, "get_id", None)
    if left_id is not None and right_id is not None:
        return type(left) is type(right) and left_id == right_id
    return left == right
