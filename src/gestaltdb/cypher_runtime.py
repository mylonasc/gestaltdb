"""Streaming runtime operators for the GestaltDB Cypher subset."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Protocol

from .cypher_ast import (
    AnchoredPatternClause,
    AndExpression,
    ArithmeticExpression,
    ComparisonExpression,
    InExpression,
    ListExpression,
    MapExpression,
    MatchQuery,
    MultiMatchQuery,
    NodePatternClause,
    NodeScanQuery,
    NotExpression,
    NullPredicate,
    OrExpression,
    Parameter,
    PathPatternClause,
    PatternHop,
    PropertyRef,
    RelationshipPatternClause,
    RelationshipScanQuery,
    StringPredicate,
    UnaryExpression,
    Variable,
    XorExpression,
)
from .cypher_plan import (
    AnchoredMatchSource,
    Expand,
    FilterNodeLabels,
    FilterNodeProperty,
    LogicalPlan,
    MatchStep,
    MultiMatchSource,
    NodeAllScan,
    NodeByIdSeek,
    NodeLabelScan,
    NodePropertySeek,
    NodeScanSource,
    ProcedureCall,
    ProcedureSource,
    ProjectItems,
    RelationshipPropertyRangeSeek,
    RelationshipPropertySeek,
    RelationshipScanSource,
    RelationshipTypeScan,
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
class BindingRow(Mapping[str, object]):
    """Typed variable bindings and traversal state for one runtime row.

    The mapping interface keeps direct runtime-helper callers compatible while
    the execution pipeline migrates away from implicit dictionary row shapes.
    """

    bindings: dict[str, object]
    current_node_id: bytes | None = None
    used_relationship_ids: frozenset[bytes] = frozenset()

    def __getitem__(self, key: str) -> object:
        if key == "bindings":
            return self.bindings
        if key == "current_node_id":
            return self.current_node_id
        if key == "used_relationship_ids":
            return self.used_relationship_ids
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("bindings", "current_node_id", "used_relationship_ids"))

    def __len__(self) -> int:
        return 3

    @classmethod
    def from_row(cls, row: BindingRow | Mapping[str, object]) -> BindingRow:
        """Return ``row`` as a typed binding row."""
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
class ProjectedRow(Mapping[str, object]):
    """Typed projected values with optional source bindings for ordering."""

    values: dict[str, object]
    source_bindings: dict[str, object] | None = None

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


class BindingOperator(Protocol):
    """Contract for an operator that transforms binding rows."""

    def execute(self, rows: Iterable[BindingRow], context: QueryContext) -> Iterator[BindingRow]: ...


class ProjectionOperator(Protocol):
    """Contract for an operator that projects binding rows."""

    def execute(self, rows: Iterable[BindingRow], context: QueryContext) -> Iterator[ProjectedRow]: ...


class ResultOperator(Protocol):
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
            if isinstance(source_row, ProjectedRow):
                bindings = source_row.values
            else:
                bindings = BindingRow.from_row(source_row).bindings
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


def execute_match(parsed: MatchQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute an anchored typed traversal plan and return projected records."""
    rows = _match_rows(parsed, context)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed, context)


def _match_rows(parsed: MatchQuery, context: QueryContext) -> Iterable[BindingRow]:
    """Produce unprojected rows for an anchored typed traversal."""
    rows = anchored_node_seek(context, parsed.source_id, parsed.source_var)
    for hop in parsed.hops:
        rows = expand_typed(context, rows, hop)
    return rows


def execute_node_scan(parsed: NodeScanQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute a label scan plan and return projected records."""
    rows = _node_scan_rows(parsed, context)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed, context)


def _node_scan_rows(
    parsed: NodeScanQuery, context: QueryContext
) -> Iterable[BindingRow]:
    """Produce unprojected rows for a node scan and inline properties."""
    node_ids = node_scan_ids(parsed, context)
    rows = hydrate_node_ids(context, node_ids, parsed.variable)
    for property_name, property_value in parsed.properties or (
        ((parsed.property_name, parsed.property_value),) if parsed.property_name is not None else ()
    ):
        rows = filter_node_property(rows, parsed.variable, property_name, context.resolve(property_value))
    return rows


def execute_relationship_scan(parsed: RelationshipScanQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute an unanchored typed relationship scan."""
    rows = _relationship_rows(parsed, context)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed, context)


def _relationship_rows(
    parsed: RelationshipScanQuery, context: QueryContext
) -> Iterable[BindingRow]:
    """Produce unprojected rows for a relationship scan."""
    return relationship_scan_rows(parsed, context)


def execute_multi_match(parsed: MultiMatchQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute multiple MATCH clauses as a streaming row pipeline."""
    rows = _multi_match_rows(parsed, context)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed, context)


def _multi_match_rows(
    parsed: MultiMatchQuery, context: QueryContext
) -> Iterable[BindingRow]:
    """Produce unprojected rows for generalized and chained MATCH clauses."""
    rows = iter([BindingRow(bindings={})])
    group_ids = parsed.match_group_ids or tuple(range(len(parsed.clauses)))
    previous_group = None
    for clause, group_id in zip(parsed.clauses, group_ids):
        if group_id != previous_group:
            rows = _reset_used_relationships(rows)
        if isinstance(clause, NodePatternClause):
            rows = apply_node_pattern_clause(rows, clause, context)
        elif isinstance(clause, RelationshipPatternClause):
            rows = apply_relationship_pattern_clause(rows, clause, context)
        elif isinstance(clause, AnchoredPatternClause):
            rows = apply_anchored_pattern_clause(rows, clause, context)
        elif isinstance(clause, PathPatternClause):
            rows = apply_path_pattern_clause(rows, clause, context)
        else:
            raise TypeError(f"Unsupported MATCH clause type: {type(clause).__name__}")
        previous_group = group_id
    rows = _reset_used_relationships(rows)
    return rows


def execute_plan(plan: LogicalPlan, context: QueryContext) -> list[dict[str, object]]:
    """Execute an authoritative logical plan against one query context."""
    if plan.staged or any(isinstance(operator, (MatchStep, ProjectItems)) for operator in plan.operators):
        return _execute_staged(plan, context)
    parsed, rows = _plan_source_rows(plan, context)
    stream: Iterable[BindingRow] | Iterable[ProjectedRow] = rows
    projected = False
    for operator in plan.operators:
        if isinstance(operator, LogicalFilterExpression):
            if projected:
                raise TypeError("FilterExpression cannot execute after projection")
            stream = filter_expression(stream, operator.expression, context)
        elif isinstance(operator, LogicalProject):
            if projected:
                raise TypeError("Logical plan contains multiple projections")
            stream = ProjectOperator(
                returns=operator.returns,
                projections=getattr(parsed, "projections", ()),
                projection_expressions=getattr(
                    parsed, "projection_expressions", ()
                ),
            ).execute(stream, context)
            projected = True
        elif isinstance(operator, LogicalSort):
            _require_projected(projected, operator)
            stream = SortOperator(operator.items, parsed).execute(stream, context)
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
        elif isinstance(
            operator,
            (
                NodeByIdSeek,
                NodeLabelScan,
                NodeAllScan,
                NodePropertySeek,
                RelationshipTypeScan,
                RelationshipPropertySeek,
                RelationshipPropertyRangeSeek,
                FilterNodeProperty,
                FilterNodeLabels,
                Expand,
                ProcedureCall,
            ),
        ):
            continue
        else:
            raise TypeError(
                f"Unsupported logical operator: {type(operator).__name__}"
            )
    if not projected:
        raise TypeError("Logical plan does not contain a projection")
    return [dict(row.values) for row in stream]


def apply_match_step(
    rows: Iterable[BindingRow], step: MatchStep, context: QueryContext
) -> Iterable[BindingRow]:
    """Match one textual ``MATCH`` clause with a fresh isomorphism scope."""
    staged: Iterable[BindingRow] = _reset_used_relationships(rows)
    for pattern in step.patterns:
        staged = apply_path_pattern_clause(staged, pattern, context)
    return staged


def filter_projected(
    rows: Iterable[ProjectedRow], expression: object, context: QueryContext
) -> Iterator[ProjectedRow]:
    """Yield projected rows whose output values satisfy a ``WHERE`` filter."""
    for row in rows:
        values = row.values if isinstance(row, ProjectedRow) else BindingRow.from_row(row).bindings
        if evaluate_expression(expression, values, context) is True:
            yield row


def _execute_staged(plan: LogicalPlan, context: QueryContext) -> list[dict[str, object]]:
    """Execute a multi-stage ``WITH`` plan, threading scopes between stages."""
    bindings: Iterable[BindingRow] | None = iter([BindingRow(bindings={})])
    projected: Iterable[ProjectedRow] | None = None
    view = ProjectionView(())
    for operator in plan.operators:
        if isinstance(operator, MatchStep):
            if projected is not None:
                bindings = (
                    BindingRow(bindings=dict(row.values), current_node_id=None)
                    for row in projected
                )
                projected = None
            bindings = apply_match_step(bindings if bindings is not None else iter(()), operator, context)
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


def _plan_source_rows(
    plan: LogicalPlan, context: QueryContext
) -> tuple[object, Iterable[BindingRow]]:
    source = plan.source
    if isinstance(source, NodeScanSource):
        return source.query, _node_scan_rows(source.query, context)
    if isinstance(source, AnchoredMatchSource):
        return source.query, _match_rows(source.query, context)
    if isinstance(source, RelationshipScanSource):
        return source.query, _relationship_rows(source.query, context)
    if isinstance(source, MultiMatchSource):
        return source.query, _multi_match_rows(source.query, context)
    if isinstance(source, ProcedureSource):
        paths = context.graph.sample_typed_paths(
            source.query.seed_ids, source.query.pattern
        )
        return source.query, (
            BindingRow(bindings={"path": path}) for path in paths
        )
    raise TypeError("Logical plan does not define a supported binding source")


def _require_projected(projected: bool, operator: object) -> None:
    if not projected:
        raise TypeError(
            f"{type(operator).__name__} cannot execute before projection"
        )


def _reset_used_relationships(rows):
    for row in rows:
        typed_row = BindingRow.from_row(row)
        yield typed_row.with_bindings(dict(typed_row.bindings), used_relationship_ids=frozenset())


def anchored_node_seek(context: QueryContext, source_id: str, source_var: str):
    """Yield one initial row for an ID lookup when the source node exists."""
    source_id_bytes = context.node_key_to_bytes(source_id)
    source_node = context.get_node(source_id_bytes)
    if source_node is None:
        return
    yield BindingRow(current_node_id=source_id_bytes, bindings={source_var: source_node})


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


def hydrate_node_ids(context: QueryContext, node_ids, variable: str):
    """Hydrate node IDs into binding rows."""
    for node_id in node_ids:
        node = context.get_node(node_id)
        if node is None:
            continue
        yield BindingRow(current_node_id=node_id, bindings={variable: node})


def apply_node_pattern_clause(rows, clause: NodePatternClause, context: QueryContext):
    """Apply a node pattern to incoming rows."""
    scan = NodeScanQuery(
        variable=clause.variable,
        label=clause.label,
        property_name=clause.property_name,
        property_value=clause.property_value,
        returns=(clause.variable,),
        labels=clause.labels,
        properties=clause.properties,
    )
    for row in rows:
        row = BindingRow.from_row(row)
        bound_node = row.bindings.get(clause.variable)
        if bound_node is not None:
            if _node_matches_clause(bound_node, clause, context):
                yield row
            continue
        for node_id in node_scan_ids(scan, context):
            node = context.get_node(node_id)
            if node is None:
                continue
            if not _node_matches_clause(node, clause, context):
                continue
            bindings = dict(row.bindings)
            bindings[clause.variable] = node
            yield row.with_bindings(bindings, current_node_id=node_id, preserve_current_node=False)


def _node_matches_clause(node, clause: NodePatternClause, context: QueryContext) -> bool:
    labels = clause.labels or ((clause.label,) if clause.label is not None else ())
    if labels and not set(labels).issubset(set(getattr(node, "labels", ()))):
        return False
    properties = clause.properties or (
        ((clause.property_name, clause.property_value),) if clause.property_name is not None else ()
    )
    for property_name, property_value in properties:
        if _cypher_equals(node.properties.get(property_name), context.resolve(property_value)) is not True:
            return False
    return True


def apply_relationship_pattern_clause(rows, clause: RelationshipPatternClause, context: QueryContext):
    """Apply a relationship pattern to incoming rows."""
    for row in rows:
        row = BindingRow.from_row(row)
        source_node = row.bindings.get(clause.source_var)
        target_node = row.bindings.get(clause.target_var)
        if source_node is not None:
            yield from _expand_relationship_from_source(row, source_node, clause, context)
            continue
        if target_node is not None:
            yield from _expand_relationship_from_target(row, target_node, clause, context)
            continue
        scan = RelationshipScanQuery(
            source_var=clause.source_var,
            rel_var=clause.rel_var,
            edge_type=clause.edge_type,
            target_var=clause.target_var,
            returns=(clause.source_var, clause.target_var),
            direction=clause.direction,
            edge_types=clause.edge_types,
        )
        for scanned_row in relationship_scan_rows(scan, context):
            bindings = dict(row.bindings)
            if _merge_bindings(bindings, scanned_row.bindings):
                yield row.with_bindings(
                    bindings,
                    current_node_id=scanned_row.current_node_id,
                    preserve_current_node=False,
                )


def _expand_relationship_from_source(row, source_node, clause: RelationshipPatternClause, context: QueryContext):
    source_id = context.node_key_to_bytes(source_node.get_id)
    direction = "any" if clause.direction == "any" else "out"
    for edge_type in clause.edge_types or (clause.edge_type,):
        for adjacency in context.graph.iter_typed_adjacency(source_id, edge_type, direction=direction):
            yield from _merge_relationship_adjacency(row, clause, context, adjacency, "source")


def _expand_relationship_from_target(row, target_node, clause: RelationshipPatternClause, context: QueryContext):
    target_id = context.node_key_to_bytes(target_node.get_id)
    direction = "any" if clause.direction == "any" else "in"
    for edge_type in clause.edge_types or (clause.edge_type,):
        for adjacency in context.graph.iter_typed_adjacency(target_id, edge_type, direction=direction):
            yield from _merge_relationship_adjacency(row, clause, context, adjacency, "target")


def _merge_relationship_adjacency(row, clause: RelationshipPatternClause, context: QueryContext, adjacency, bound_endpoint):
    edge = context.get_edge(adjacency["edge_id"])
    if edge is None:
        return
    source_id = context.node_key_to_bytes(edge.source)
    target_id = context.node_key_to_bytes(edge.target)
    source_node = context.get_node(source_id)
    target_node = context.get_node(target_id)
    if source_node is None or target_node is None:
        return
    if clause.direction == "any":
        bound_variable = clause.source_var if bound_endpoint == "source" else clause.target_var
        bound_node = row.bindings[bound_variable]
        neighbor_node = context.get_node(adjacency["neighbor_id"])
        if neighbor_node is None:
            return
        if bound_endpoint == "source":
            new_bindings = {clause.source_var: bound_node, clause.target_var: neighbor_node}
        else:
            new_bindings = {clause.source_var: neighbor_node, clause.target_var: bound_node}
    else:
        new_bindings = {clause.source_var: source_node, clause.target_var: target_node}
    if clause.rel_var is not None:
        new_bindings[clause.rel_var] = edge
    bindings = dict(row.bindings)
    if not _merge_bindings(bindings, new_bindings):
        return
    current_node_id = target_id if clause.direction == "out" else source_id
    typed_row = BindingRow.from_row(row)
    yield typed_row.with_bindings(bindings, current_node_id=current_node_id, preserve_current_node=False)


def apply_anchored_pattern_clause(rows, clause: AnchoredPatternClause, context: QueryContext):
    """Apply an anchored traversal clause to incoming rows."""
    source_id_bytes = context.node_key_to_bytes(clause.source_id)
    source_node = context.get_node(source_id_bytes)
    if source_node is None:
        return
    for row in rows:
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        if clause.source_var in bindings and not same_entity(bindings[clause.source_var], source_node):
            continue
        bindings[clause.source_var] = source_node
        expanded = iter(
            [
                row.with_bindings(
                    bindings,
                    current_node_id=source_id_bytes,
                    preserve_current_node=False,
                    used_relationship_ids=frozenset(),
                )
            ]
        )
        for hop in clause.hops:
            expanded = expand_typed(context, expanded, hop)
        for expanded_row in expanded:
            yield BindingRow.from_row(expanded_row).with_bindings(
                dict(expanded_row.bindings), used_relationship_ids=frozenset()
            )


def apply_path_pattern_clause(rows, clause: PathPatternClause, context: QueryContext):
    """Apply a generalized fixed-length path pattern to incoming rows."""
    for row in rows:
        row = BindingRow.from_row(row)
        starts = _path_start_rows(row, clause, context)
        expanded = starts
        for hop in clause.hops:
            expanded = _expand_pattern_hop(context, expanded, hop)
        yield from expanded


def _path_start_rows(row, clause: PathPatternClause, context: QueryContext):
    source = clause.source
    identity = next((value for name, value in source.properties if name == "id"), None)
    properties = tuple(item for item in source.properties if item[0] != "id") if identity is not None else source.properties
    bound_node = row.bindings.get(source.variable) if source.variable is not None else None
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


def _merge_bindings(bindings: dict[str, object], new_bindings: dict[str, object]) -> bool:
    for variable, value in new_bindings.items():
        if variable in bindings and not same_entity(bindings[variable], value):
            return False
        bindings[variable] = value
    return True


def relationship_scan_rows(parsed: RelationshipScanQuery, context: QueryContext):
    """Yield binding rows from relationship type/property index scans."""
    seen = set()
    for edge_type in parsed.edge_types or (parsed.edge_type,):
        for edge_id in _relationship_scan_edge_ids(parsed, context, edge_type):
            if edge_id in seen:
                continue
            seen.add(edge_id)
            yield from _hydrate_relationship_scan_edge(context, parsed, edge_id)


def _relationship_scan_edge_ids(parsed: RelationshipScanQuery, context: QueryContext, edge_type: str):
    exact_scan = _relationship_exact_scan(parsed, context, edge_type)
    if exact_scan is not None:
        return exact_scan
    range_scan = _relationship_range_scan(parsed, context, edge_type)
    if range_scan is not None:
        return range_scan
    return context.graph.iter_edge_ids_by_type(edge_type)


def _relationship_exact_scan(parsed: RelationshipScanQuery, context: QueryContext, edge_type: str):
    if parsed.rel_var is None or parsed.where is None:
        return None
    expressions = parsed.where.expressions if isinstance(parsed.where, AndExpression) else (parsed.where,)
    for expression in expressions:
        if not isinstance(expression, ComparisonExpression):
            continue
        if not isinstance(expression.left, PropertyRef):
            continue
        if not _is_seek_value(expression.right):
            continue
        if expression.left.variable != parsed.rel_var or expression.operator != "=":
            continue
        if expression.left.property_name not in getattr(context.graph, "indexed_edge_properties", set()):
            continue
        if hasattr(context.graph, "iter_edge_ids_by_type_property"):
            return context.graph.iter_edge_ids_by_type_property(edge_type, expression.left.property_name, context.resolve(expression.right))
    return None


def _relationship_range_scan(parsed: RelationshipScanQuery, context: QueryContext, edge_type: str):
    bounds = _range_bounds_for_relationship_scan(parsed, context)
    if bounds is None:
        return None
    property_name, start_value, end_value, include_start, include_end = bounds
    if hasattr(context.graph, "iter_edge_ids_by_type_property_range"):
        return context.graph.iter_edge_ids_by_type_property_range(edge_type, property_name, start_value, end_value, include_start, include_end)
    return None


def _range_bounds_for_relationship_scan(parsed: RelationshipScanQuery, context: QueryContext):
    if parsed.rel_var is None or parsed.where is None:
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
        if expression.left.variable != parsed.rel_var:
            continue
        if expression.operator not in {"<", "<=", ">", ">="}:
            continue
        if expression.left.property_name not in getattr(context.graph, "indexed_edge_properties", set()):
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
    if not found:
        return None
    return property_name, start_value, end_value, include_start, include_end


def _hydrate_relationship_scan_edge(context: QueryContext, parsed: RelationshipScanQuery, edge_id: bytes):
    edge = context.get_edge(edge_id)
    if edge is None:
        return
    source_id = context.node_key_to_bytes(edge.source)
    target_id = context.node_key_to_bytes(edge.target)
    source_node = context.get_node(source_id)
    target_node = context.get_node(target_id)
    if source_node is None or target_node is None:
        return
    orientations = [(source_node, target_node, target_id)]
    if parsed.direction == "any" and source_id != target_id:
        orientations.append((target_node, source_node, source_id))
    for left_node, right_node, current_node_id in orientations:
        bindings = {}
        if not _merge_bindings(bindings, {parsed.source_var: left_node}):
            continue
        if not _merge_bindings(bindings, {parsed.target_var: right_node}):
            continue
        if parsed.rel_var is not None and not _merge_bindings(bindings, {parsed.rel_var: edge}):
            continue
        yield BindingRow(current_node_id=current_node_id, bindings=bindings)


def filter_node_property(rows, variable: str, property_name: str, property_value):
    """Yield rows whose bound node has an exact property value."""
    for row in rows:
        row = BindingRow.from_row(row)
        node = row.bindings[variable]
        if _cypher_equals(node.properties.get(property_name), property_value) is True:
            yield row


def filter_expression(rows, expression, context: QueryContext):
    """Yield rows that satisfy a supported boolean expression."""
    for row in rows:
        row = BindingRow.from_row(row)
        if evaluate_expression(expression, row.bindings, context) is True:
            yield row


def evaluate_expression(expression, bindings: dict[str, object], context: QueryContext):
    """Evaluate an expression using Cypher null propagation and boolean logic."""
    if isinstance(expression, Parameter):
        return context.resolve(expression)
    if isinstance(expression, Variable):
        return bindings[expression.name]
    if isinstance(expression, PropertyRef):
        return project_value(bindings, f"{expression.variable}.{expression.property_name}")
    if isinstance(expression, ListExpression):
        return [evaluate_expression(item, bindings, context) for item in expression.items]
    if isinstance(expression, MapExpression):
        return {key: evaluate_expression(value, bindings, context) for key, value in expression.items}
    if isinstance(expression, list):
        return [evaluate_expression(item, bindings, context) for item in expression]
    if isinstance(expression, dict):
        return {key: evaluate_expression(value, bindings, context) for key, value in expression.items()}
    if isinstance(expression, NotExpression):
        value = _boolean_value(evaluate_expression(expression.expression, bindings, context))
        return None if value is None else not value
    if isinstance(expression, AndExpression):
        result = True
        for part in expression.expressions:
            value = _boolean_value(evaluate_expression(part, bindings, context))
            if value is False:
                return False
            if value is None:
                result = None
        return result
    if isinstance(expression, OrExpression):
        result = False
        for part in expression.expressions:
            value = _boolean_value(evaluate_expression(part, bindings, context))
            if value is True:
                return True
            if value is None:
                result = None
        return result
    if isinstance(expression, XorExpression):
        values = [_boolean_value(evaluate_expression(part, bindings, context)) for part in expression.expressions]
        if any(value is None for value in values):
            return None
        return sum(value is True for value in values) % 2 == 1
    if isinstance(expression, InExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        values = evaluate_expression(expression.values, bindings, context)
        if values is None:
            return None
        if not isinstance(values, (list, tuple)):
            raise TypeError("IN expects a list value")
        saw_null = left_value is None
        for value in values:
            equal = _cypher_equals(left_value, value)
            if equal is True:
                return True
            saw_null = saw_null or equal is None
        return None if saw_null else False
    if isinstance(expression, NullPredicate):
        value = evaluate_expression(expression.expression, bindings, context)
        return value is not None if expression.negated else value is None
    if isinstance(expression, StringPredicate):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        if left_value is None or right_value is None:
            return None
        if not isinstance(left_value, str) or not isinstance(right_value, str):
            raise TypeError(f"{expression.operator} expects string operands")
        if expression.operator == "STARTS WITH":
            return left_value.startswith(right_value)
        if expression.operator == "ENDS WITH":
            return left_value.endswith(right_value)
        return right_value in left_value
    if isinstance(expression, UnaryExpression):
        value = evaluate_expression(expression.expression, bindings, context)
        if value is None:
            return None
        _require_number(value, expression.operator)
        return value if expression.operator == "+" else -value
    if isinstance(expression, ArithmeticExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        if left_value is None or right_value is None:
            return None
        if expression.operator == "+" and (isinstance(left_value, str) or isinstance(right_value, str)):
            if not isinstance(left_value, str) or not isinstance(right_value, str):
                raise TypeError("+ expects two strings or two numbers")
            return left_value + right_value
        _require_number(left_value, expression.operator)
        _require_number(right_value, expression.operator)
        operations = {
            "+": lambda: left_value + right_value,
            "-": lambda: left_value - right_value,
            "*": lambda: left_value * right_value,
            "/": lambda: left_value / right_value,
            "%": lambda: left_value % right_value,
        }
        return operations[expression.operator]()
    if isinstance(expression, ComparisonExpression):
        left_value = evaluate_expression(expression.left, bindings, context)
        right_value = evaluate_expression(expression.right, bindings, context)
        operator = expression.operator
        if operator == "=":
            return _cypher_equals(left_value, right_value)
        if operator in {"!=", "<>"}:
            equal = _cypher_equals(left_value, right_value)
            return None if equal is None else not equal
        if left_value is None or right_value is None:
            return None
        if operator == "=~":
            if not isinstance(left_value, str) or not isinstance(right_value, str):
                raise TypeError("=~ expects string operands")
            return re.fullmatch(right_value, left_value) is not None
        try:
            return {
                "<": lambda: left_value < right_value,
                "<=": lambda: left_value <= right_value,
                ">": lambda: left_value > right_value,
                ">=": lambda: left_value >= right_value,
            }[operator]()
        except TypeError as exc:
            raise TypeError(f"Cannot compare {type(left_value).__name__} and {type(right_value).__name__}") from exc
    return expression


def _cypher_equals(left, right):
    if left is None or right is None:
        return None
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        comparisons = [_cypher_equals(left_item, right_item) for left_item, right_item in zip(left, right)]
        if False in comparisons:
            return False
        return None if None in comparisons else True
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        comparisons = [_cypher_equals(left[key], right[key]) for key in left]
        if False in comparisons:
            return False
        return None if None in comparisons else True
    return left == right


def _boolean_value(value):
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"Expected boolean expression, got {type(value).__name__}")


def _require_number(value, operator: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{operator} expects numeric operands")


def expand_typed(context: QueryContext, rows, hop):
    """Expand rows through one typed relationship hop."""
    for row in rows:
        row = BindingRow.from_row(row)
        seen = set()
        for edge_type in hop.edge_types or (hop.edge_type,):
            for adjacency in context.graph.iter_typed_adjacency(
                row.current_node_id,
                edge_type,
                direction=hop.direction,
            ):
                edge_id = adjacency["edge_id"]
                occurrence = (edge_id, adjacency["neighbor_id"])
                if occurrence in seen or edge_id in row.used_relationship_ids:
                    continue
                seen.add(occurrence)
                target_node = context.get_node(adjacency["neighbor_id"])
                if target_node is None:
                    continue
                bindings = dict(row.bindings)
                if hop.target_var in bindings and not same_entity(bindings[hop.target_var], target_node):
                    continue
                bindings[hop.target_var] = target_node
                if hop.rel_var is not None:
                    edge = context.get_edge(adjacency["edge_id"])
                    if edge is None:
                        continue
                    if hop.rel_var in bindings and not same_entity(bindings[hop.rel_var], edge):
                        continue
                    bindings[hop.rel_var] = edge
                yield row.with_bindings(
                    bindings,
                    current_node_id=adjacency["neighbor_id"],
                    preserve_current_node=False,
                    used_relationship_ids=row.used_relationship_ids.union((edge_id,)),
                )


def limit_rows(rows, limit: int | None):
    """Limit a streaming row source."""
    if limit is None:
        return rows
    return islice(rows, limit)


def project_rows(rows, returns: tuple[str, ...], projections: tuple[str, ...] = (), projection_expressions=(), limit: int | None = None, context: QueryContext | None = None):
    """Project binding rows into result records."""
    limited_rows = limit_rows(rows, limit)
    projection_items = projections or returns
    for row in limited_rows:
        row = BindingRow.from_row(row)
        if projection_expressions and context is not None:
            yield {
                column: evaluate_expression(expression, row.bindings, context)
                for column, expression in zip(returns, projection_expressions)
            }
        else:
            yield {
                column: project_value(row.bindings, projection)
                for column, projection in zip(returns, projection_items)
            }


def materialize_results(rows, parsed, context: QueryContext) -> list[dict[str, object]]:
    """Apply result shaping and return projected records."""
    skip = _resolve_pagination(parsed.skip, context, "SKIP")
    limit = _resolve_pagination(parsed.limit, context, "LIMIT")
    projection_expressions = getattr(parsed, "projection_expressions", ())
    projected_rows: Iterable[ProjectedRow] = ProjectOperator(
        returns=parsed.returns,
        projections=parsed.projections,
        projection_expressions=projection_expressions,
    ).execute(rows, context)
    if parsed.order_by:
        projected_rows = SortOperator(parsed.order_by, parsed).execute(
            projected_rows, context
        )
    if parsed.distinct:
        projected_rows = DistinctOperator(parsed.returns).execute(
            projected_rows, context
        )
    if skip is not None:
        projected_rows = SkipOperator(skip).execute(projected_rows, context)
    if limit is not None:
        projected_rows = LimitOperator(limit).execute(projected_rows, context)
    return [dict(row.values) for row in projected_rows]


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


def _is_seek_value(value) -> bool:
    return value is None or isinstance(value, (Parameter, str, int, float, bool, list, dict))


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


def project_value(bindings: dict[str, object], return_item: str):
    """Project one return item from variable bindings."""
    variable, _, property_name = return_item.partition(".")
    value = bindings[variable]
    if not property_name:
        return value
    if property_name == "id" and hasattr(value, "get_id"):
        return value.get_id
    if property_name == "labels" and hasattr(value, "labels"):
        return value.labels
    if property_name in {"source", "target"} and hasattr(value, property_name):
        return getattr(value, property_name)
    properties = getattr(value, "properties", {})
    if property_name in properties:
        return properties[property_name]
    return None


def same_entity(left, right) -> bool:
    """Return whether two graph entities represent the same stored entity."""
    left_id = getattr(left, "get_id", None)
    right_id = getattr(right, "get_id", None)
    if left_id is not None and right_id is not None:
        return type(left) is type(right) and left_id == right_id
    return left == right
