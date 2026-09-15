"""Logical planning for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass

from .cypher_ast import (
    AnchoredPatternClause,
    AndExpression,
    ComparisonExpression,
    MatchClause,
    MatchQuery,
    MultiMatchQuery,
    NodePatternClause,
    NodeScanQuery,
    PathPatternClause,
    PatternHop,
    PropertyRef,
    Query,
    RelationshipPatternClause,
    RelationshipScanQuery,
    ReturnClause,
    SampleTypedPathsCall,
    TraversalHop,
    WhereClause,
    WithClause,
)


@dataclass(frozen=True)
class LogicalPlan:
    """Ordered logical operators for a parsed query."""

    operators: tuple[object, ...]
    source: object | None = None
    columns: tuple[str, ...] = ()
    staged: bool = False


@dataclass(frozen=True)
class MatchStep:
    """Match the patterns of one textual ``MATCH`` with a fresh scope."""

    patterns: tuple[PathPatternClause, ...]
    group_id: int


@dataclass(frozen=True)
class ProjectItems:
    """Project one ``WITH`` or ``RETURN`` clause from resolved expressions."""

    returns: tuple[str, ...]
    expressions: tuple[object, ...] = ()


@dataclass(frozen=True)
class NodeScanSource:
    """Produce binding rows for one parsed node scan."""

    query: NodeScanQuery


@dataclass(frozen=True)
class AnchoredMatchSource:
    """Produce binding rows for one anchored typed traversal."""

    query: MatchQuery


@dataclass(frozen=True)
class RelationshipScanSource:
    """Produce binding rows for one relationship scan."""

    query: RelationshipScanQuery


@dataclass(frozen=True)
class MultiMatchSource:
    """Produce binding rows for generalized and chained patterns."""

    query: MultiMatchQuery


@dataclass(frozen=True)
class ProcedureSource:
    """Produce binding rows from a supported procedure call."""

    query: SampleTypedPathsCall


@dataclass(frozen=True)
class NodeByIdSeek:
    """Seek one node by its identity value."""

    source_id: str
    variable: str


@dataclass(frozen=True)
class NodeLabelScan:
    """Scan node IDs from the label index."""

    label: str
    variable: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeAllScan:
    """Scan all node IDs from the node store."""

    variable: str


@dataclass(frozen=True)
class NodePropertySeek:
    """Seek node IDs from an exact property index."""

    property_name: str
    property_value: object


@dataclass(frozen=True)
class RelationshipTypeScan:
    """Scan relationship IDs from the relationship type catalog."""

    edge_types: tuple[str, ...]
    rel_var: str | None


@dataclass(frozen=True)
class RelationshipPropertySeek:
    """Seek relationship IDs from a composite type/property exact index."""

    rel_var: str
    property_name: str
    property_value: object


@dataclass(frozen=True)
class RelationshipPropertyRangeSeek:
    """Seek relationship IDs from a composite type/property range index."""

    rel_var: str
    property_name: str
    operator: str
    property_value: object


@dataclass(frozen=True)
class FilterNodeProperty:
    """Filter bound nodes by exact property value."""

    variable: str
    property_name: str
    property_value: object


@dataclass(frozen=True)
class FilterNodeLabels:
    """Filter a bound node by required labels."""

    variable: str | None
    labels: tuple[str, ...]


@dataclass(frozen=True)
class FilterExpression:
    """Filter rows by a boolean expression."""

    expression: object


@dataclass(frozen=True)
class Expand:
    """Expand rows through one typed relationship hop."""

    hop: TraversalHop | PatternHop


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


def plan_query(parsed) -> LogicalPlan:
    """Create a simple logical plan for the currently supported query types."""
    if isinstance(parsed, NodeScanQuery):
        return _plan_node_scan(parsed)
    if isinstance(parsed, MatchQuery):
        return _plan_match(parsed)
    if isinstance(parsed, RelationshipScanQuery):
        return _plan_relationship_scan(parsed)
    if isinstance(parsed, MultiMatchQuery):
        return _plan_multi_match(parsed)
    if isinstance(parsed, SampleTypedPathsCall):
        operators = [ProcedureCall("pg.sample_typed_paths"), Project(parsed.returns)]
        _append_result_operators(operators, parsed)
        return LogicalPlan(
            tuple(operators), ProcedureSource(parsed), parsed.returns
        )
    raise TypeError(f"unsupported parsed query type: {type(parsed).__name__}")


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
        if isinstance(clause, MatchClause):
            operators.append(MatchStep(clause.patterns, group_id))
            group_id += 1
        elif isinstance(clause, WhereClause):
            operators.append(FilterExpression(clause.expression))
        elif isinstance(clause, (WithClause, ReturnClause)):
            entry = by_clause[id(clause)]
            operators.append(
                ProjectItems(
                    returns=tuple(item.output.name for item in entry.projections),
                    expressions=tuple(item.expression for item in entry.projections),
                )
            )
            if clause.distinct:
                operators.append(Distinct())
            if index + 1 < len(clauses) and isinstance(clauses[index + 1], WhereClause):
                operators.append(FilterExpression(clauses[index + 1].expression))
                index += 1
            if clause.order_by:
                operators.append(Sort(clause.order_by))
            if clause.skip is not None:
                operators.append(Skip(clause.skip))
            if clause.limit is not None:
                operators.append(Limit(clause.limit))
        else:
            raise TypeError(f"unsupported canonical clause type: {type(clause).__name__}")
        index += 1
    return LogicalPlan(tuple(operators), None, analysis.output_names, True)


def _plan_node_scan(parsed: NodeScanQuery) -> LogicalPlan:
    if parsed.labels or parsed.label is not None:
        operators: list[object] = [NodeLabelScan(parsed.label, parsed.variable, parsed.labels or (parsed.label,))]
    else:
        operators = [NodeAllScan(parsed.variable)]
    if parsed.property_name is not None:
        operators.append(NodePropertySeek(parsed.property_name, parsed.property_value))
        operators.append(FilterNodeProperty(parsed.variable, parsed.property_name, parsed.property_value))
    if parsed.where is not None:
        operators.append(FilterExpression(parsed.where))
    operators.append(Project(parsed.returns))
    _append_result_operators(operators, parsed)
    return LogicalPlan(tuple(operators), NodeScanSource(parsed), parsed.returns)


def _plan_match(parsed: MatchQuery) -> LogicalPlan:
    operators: list[object] = [NodeByIdSeek(parsed.source_id, parsed.source_var)]
    operators.extend(Expand(hop) for hop in parsed.hops)
    if parsed.where is not None:
        operators.append(FilterExpression(parsed.where))
    operators.append(Project(parsed.returns))
    _append_result_operators(operators, parsed)
    return LogicalPlan(tuple(operators), AnchoredMatchSource(parsed), parsed.returns)


def _plan_relationship_scan(parsed: RelationshipScanQuery) -> LogicalPlan:
    operators: list[object] = [RelationshipTypeScan(parsed.edge_types or (parsed.edge_type,), parsed.rel_var)]
    operators.extend(_relationship_property_seek_operators(parsed))
    if parsed.where is not None:
        operators.append(FilterExpression(parsed.where))
    operators.append(Project(parsed.returns))
    _append_result_operators(operators, parsed)
    return LogicalPlan(
        tuple(operators), RelationshipScanSource(parsed), parsed.returns
    )


def _plan_multi_match(parsed: MultiMatchQuery) -> LogicalPlan:
    operators: list[object] = []
    for clause in parsed.clauses:
        if isinstance(clause, NodePatternClause):
            if clause.labels or clause.label is not None:
                operators.append(NodeLabelScan(clause.label, clause.variable, clause.labels or (clause.label,)))
            else:
                operators.append(NodeAllScan(clause.variable))
            if clause.property_name is not None:
                operators.append(NodePropertySeek(clause.property_name, clause.property_value))
                operators.append(FilterNodeProperty(clause.variable, clause.property_name, clause.property_value))
        elif isinstance(clause, RelationshipPatternClause):
            operators.append(RelationshipTypeScan(clause.edge_types or (clause.edge_type,), clause.rel_var))
        elif isinstance(clause, AnchoredPatternClause):
            operators.append(NodeByIdSeek(clause.source_id, clause.source_var))
            operators.extend(Expand(hop) for hop in clause.hops)
        elif isinstance(clause, PathPatternClause):
            variable = clause.source.variable or ""
            source_properties = dict(clause.source.properties)
            if "id" in source_properties:
                operators.append(NodeByIdSeek(source_properties.pop("id"), variable))
            elif clause.source.labels:
                operators.append(NodeLabelScan(clause.source.labels[0], variable, clause.source.labels))
            else:
                operators.append(NodeAllScan(variable))
            if clause.source.labels:
                operators.append(FilterNodeLabels(clause.source.variable, clause.source.labels))
            operators.extend(
                FilterNodeProperty(variable, property_name, property_value)
                for property_name, property_value in source_properties.items()
            )
            for hop in clause.hops:
                operators.append(Expand(hop))
                if hop.target.labels:
                    operators.append(FilterNodeLabels(hop.target.variable, hop.target.labels))
                operators.extend(
                    FilterNodeProperty(hop.target.variable or "", property_name, property_value)
                    for property_name, property_value in hop.target.properties
                )
    if parsed.where is not None:
        operators.append(FilterExpression(parsed.where))
    operators.append(Project(parsed.returns))
    _append_result_operators(operators, parsed)
    return LogicalPlan(tuple(operators), MultiMatchSource(parsed), parsed.returns)


def _relationship_property_seek_operators(parsed: RelationshipScanQuery) -> list[object]:
    if parsed.rel_var is None or parsed.where is None:
        return []
    operators = []
    expressions = parsed.where.expressions if isinstance(parsed.where, AndExpression) else (parsed.where,)
    for expression in expressions:
        if not isinstance(expression, ComparisonExpression):
            continue
        if not isinstance(expression.left, PropertyRef):
            continue
        if not _is_seek_value(expression.right):
            continue
        if expression.left.variable != parsed.rel_var:
            continue
        if expression.operator == "=":
            operators.append(RelationshipPropertySeek(parsed.rel_var, expression.left.property_name, expression.right))
        elif expression.operator in {"<", "<=", ">", ">="}:
            operators.append(RelationshipPropertyRangeSeek(parsed.rel_var, expression.left.property_name, expression.operator, expression.right))
    return operators


def _append_result_operators(operators: list[object], parsed) -> None:
    if getattr(parsed, "order_by", ()):
        operators.append(Sort(parsed.order_by))
    if getattr(parsed, "distinct", False):
        operators.append(Distinct())
    if getattr(parsed, "skip", None) is not None:
        operators.append(Skip(parsed.skip))
    if parsed.limit is not None:
        operators.append(Limit(parsed.limit))


def _is_seek_value(value) -> bool:
    from .cypher_ast import Parameter

    return value is None or isinstance(value, (Parameter, str, int, float, bool, list, dict))
