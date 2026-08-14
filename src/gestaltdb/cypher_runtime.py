"""Streaming runtime operators for the GestaltDB Cypher subset."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice

from .cypher_ast import AnchoredPatternClause, AndExpression, ComparisonExpression, InExpression, MatchQuery, MultiMatchQuery, NodePatternClause, NodeScanQuery, NullPredicate, Parameter, RelationshipPatternClause, RelationshipScanQuery


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
        return value


def execute_match(parsed: MatchQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute an anchored typed traversal plan and return projected records."""
    rows = anchored_node_seek(context, parsed.source_id, parsed.source_var)
    for hop in parsed.hops:
        rows = expand_typed(context, rows, hop)
        can_push_limit = parsed.where is None and not parsed.order_by and parsed.skip is None and not parsed.distinct
        if parsed.limit is not None and can_push_limit:
            rows = limit_rows(rows, parsed.limit)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed)


def execute_node_scan(parsed: NodeScanQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute a label scan plan and return projected records."""
    node_ids = node_scan_ids(parsed, context)
    rows = hydrate_node_ids(context, node_ids, parsed.variable)
    if parsed.property_name is not None:
        property_value = context.resolve(parsed.property_value)
        rows = filter_node_property(rows, parsed.variable, parsed.property_name, property_value)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed)


def execute_relationship_scan(parsed: RelationshipScanQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute an unanchored typed relationship scan."""
    rows = relationship_scan_rows(parsed, context)
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed)


def execute_multi_match(parsed: MultiMatchQuery, context: QueryContext) -> list[dict[str, object]]:
    """Execute multiple MATCH clauses as a streaming row pipeline."""
    rows = iter([{"current_node_id": None, "bindings": {}}])
    for clause in parsed.clauses:
        if isinstance(clause, NodePatternClause):
            rows = apply_node_pattern_clause(rows, clause, context)
        elif isinstance(clause, RelationshipPatternClause):
            rows = apply_relationship_pattern_clause(rows, clause, context)
        elif isinstance(clause, AnchoredPatternClause):
            rows = apply_anchored_pattern_clause(rows, clause, context)
        else:
            raise ValueError(f"Unsupported MATCH clause type: {type(clause).__name__}")
    if parsed.where is not None:
        rows = filter_expression(rows, parsed.where, context)
    return materialize_results(rows, parsed)


def anchored_node_seek(context: QueryContext, source_id: str, source_var: str):
    """Yield one initial row for an ID lookup when the source node exists."""
    source_id_bytes = context.node_key_to_bytes(source_id)
    source_node = context.get_node(source_id_bytes)
    if source_node is None:
        return
    yield {
        "current_node_id": source_id_bytes,
        "bindings": {source_var: source_node},
    }


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
        yield {
            "current_node_id": node_id,
            "bindings": {variable: node},
        }


def apply_node_pattern_clause(rows, clause: NodePatternClause, context: QueryContext):
    """Apply a node pattern to incoming rows."""
    scan = NodeScanQuery(
        variable=clause.variable,
        label=clause.label,
        property_name=clause.property_name,
        property_value=clause.property_value,
        returns=(clause.variable,),
        labels=clause.labels,
    )
    for row in rows:
        bound_node = row["bindings"].get(clause.variable)
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
            bindings = dict(row["bindings"])
            bindings[clause.variable] = node
            yield {"current_node_id": node_id, "bindings": bindings}


def _node_matches_clause(node, clause: NodePatternClause, context: QueryContext) -> bool:
    labels = clause.labels or ((clause.label,) if clause.label is not None else ())
    if labels and not set(labels).issubset(set(getattr(node, "labels", ()))):
        return False
    if clause.property_name is not None:
        return node.properties.get(clause.property_name) == context.resolve(clause.property_value)
    return True


def apply_relationship_pattern_clause(rows, clause: RelationshipPatternClause, context: QueryContext):
    """Apply a relationship pattern to incoming rows."""
    for row in rows:
        source_node = row["bindings"].get(clause.source_var)
        target_node = row["bindings"].get(clause.target_var)
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
            bindings = dict(row["bindings"])
            if _merge_bindings(bindings, scanned_row["bindings"]):
                yield {"current_node_id": scanned_row["current_node_id"], "bindings": bindings}


def _expand_relationship_from_source(row, source_node, clause: RelationshipPatternClause, context: QueryContext):
    source_id = context.node_key_to_bytes(source_node.get_id)
    for edge_type in clause.edge_types or (clause.edge_type,):
        for adjacency in context.graph.iter_typed_adjacency(source_id, edge_type, direction=clause.direction):
            yield from _merge_relationship_adjacency(row, clause, context, adjacency)


def _expand_relationship_from_target(row, target_node, clause: RelationshipPatternClause, context: QueryContext):
    target_id = context.node_key_to_bytes(target_node.get_id)
    direction = {"out": "in", "in": "out"}.get(clause.direction, "any")
    for edge_type in clause.edge_types or (clause.edge_type,):
        for adjacency in context.graph.iter_typed_adjacency(target_id, edge_type, direction=direction):
            yield from _merge_relationship_adjacency(row, clause, context, adjacency)


def _merge_relationship_adjacency(row, clause: RelationshipPatternClause, context: QueryContext, adjacency):
    edge = context.get_edge(adjacency["edge_id"])
    if edge is None:
        return
    source_id = context.node_key_to_bytes(edge.source)
    target_id = context.node_key_to_bytes(edge.target)
    source_node = context.get_node(source_id)
    target_node = context.get_node(target_id)
    if source_node is None or target_node is None:
        return
    new_bindings = {clause.source_var: source_node, clause.target_var: target_node}
    if clause.rel_var is not None:
        new_bindings[clause.rel_var] = edge
    bindings = dict(row["bindings"])
    if not _merge_bindings(bindings, new_bindings):
        return
    current_node_id = target_id if clause.direction == "out" else source_id
    yield {"current_node_id": current_node_id, "bindings": bindings}


def apply_anchored_pattern_clause(rows, clause: AnchoredPatternClause, context: QueryContext):
    """Apply an anchored traversal clause to incoming rows."""
    source_id_bytes = context.node_key_to_bytes(clause.source_id)
    source_node = context.get_node(source_id_bytes)
    if source_node is None:
        return
    for row in rows:
        bindings = dict(row["bindings"])
        if clause.source_var in bindings and not same_entity(bindings[clause.source_var], source_node):
            continue
        bindings[clause.source_var] = source_node
        expanded = iter([{"current_node_id": source_id_bytes, "bindings": bindings}])
        for hop in clause.hops:
            expanded = expand_typed(context, expanded, hop)
        yield from expanded


def _merge_bindings(bindings: dict[str, object], new_bindings: dict[str, object]) -> bool:
    for variable, value in new_bindings.items():
        if variable in bindings and not same_entity(bindings[variable], value):
            return False
        bindings[variable] = value
    return True


def relationship_scan_rows(parsed: RelationshipScanQuery, context: QueryContext):
    """Yield binding rows from relationship type/property index scans."""
    for edge_type in parsed.edge_types or (parsed.edge_type,):
        for edge_id in _relationship_scan_edge_ids(parsed, context, edge_type):
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
    bindings = {
        parsed.source_var: source_node,
        parsed.target_var: target_node,
    }
    if parsed.rel_var is not None:
        bindings[parsed.rel_var] = edge
    current_node_id = target_id if parsed.direction == "out" else source_id
    yield {
        "current_node_id": current_node_id,
        "bindings": bindings,
    }


def filter_node_property(rows, variable: str, property_name: str, property_value):
    """Yield rows whose bound node has an exact property value."""
    for row in rows:
        node = row["bindings"][variable]
        if node.properties.get(property_name) == property_value:
            yield row


def filter_expression(rows, expression, context: QueryContext):
    """Yield rows that satisfy a supported boolean expression."""
    for row in rows:
        if evaluate_expression(expression, row["bindings"], context):
            yield row


def evaluate_expression(expression, bindings: dict[str, object], context: QueryContext) -> bool:
    """Evaluate a supported expression against one row of bindings."""
    if isinstance(expression, AndExpression):
        return all(evaluate_expression(part, bindings, context) for part in expression.expressions)
    if isinstance(expression, InExpression):
        left_value = project_value(bindings, f"{expression.left.variable}.{expression.left.property_name}")
        values = context.resolve(expression.values)
        if not isinstance(values, (list, tuple, set)):
            raise ValueError("IN expects a list, tuple, or set value")
        return left_value in values
    if isinstance(expression, NullPredicate):
        value = project_value(bindings, f"{expression.expression.variable}.{expression.expression.property_name}")
        return value is not None if expression.negated else value is None
    if not isinstance(expression, ComparisonExpression):
        raise ValueError(f"Unsupported expression type: {type(expression).__name__}")
    left_value = project_value(bindings, f"{expression.left.variable}.{expression.left.property_name}")
    right_value = context.resolve(expression.right)
    operator = expression.operator
    if operator == "=":
        return left_value == right_value
    if operator in {"!=", "<>"}:
        return left_value != right_value
    if left_value is None or right_value is None:
        return False
    if operator == "<":
        return left_value < right_value
    if operator == "<=":
        return left_value <= right_value
    if operator == ">":
        return left_value > right_value
    if operator == ">=":
        return left_value >= right_value
    raise ValueError(f"Unsupported comparison operator: {operator}")


def expand_typed(context: QueryContext, rows, hop):
    """Expand rows through one typed relationship hop."""
    for row in rows:
        for edge_type in hop.edge_types or (hop.edge_type,):
            for adjacency in context.graph.iter_typed_adjacency(
                row["current_node_id"],
                edge_type,
                direction=hop.direction,
            ):
                target_node = context.get_node(adjacency["neighbor_id"])
                if target_node is None:
                    continue
                bindings = dict(row["bindings"])
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
                yield {
                    "current_node_id": adjacency["neighbor_id"],
                    "bindings": bindings,
                }


def limit_rows(rows, limit: int | None):
    """Limit a streaming row source."""
    if limit is None:
        return rows
    return islice(rows, limit)


def project_rows(rows, returns: tuple[str, ...], projections: tuple[str, ...] = (), limit: int | None = None):
    """Project binding rows into result records."""
    limited_rows = limit_rows(rows, limit)
    projection_items = projections or returns
    for row in limited_rows:
        yield {column: project_value(row["bindings"], projection) for column, projection in zip(returns, projection_items)}


def materialize_results(rows, parsed) -> list[dict[str, object]]:
    """Apply result shaping and return projected records."""
    if not parsed.order_by and not parsed.distinct and parsed.skip is None:
        return list(project_rows(rows, parsed.returns, projections=parsed.projections, limit=parsed.limit))
    row_list = list(rows)
    if parsed.order_by:
        for order_item in reversed(parsed.order_by):
            row_list.sort(
                key=lambda row, expression=order_item.expression: _sortable_value(project_value(row["bindings"], expression)),
                reverse=order_item.descending,
            )
    records = list(project_rows(row_list, parsed.returns, projections=parsed.projections))
    if parsed.distinct:
        records = _distinct_records(records, parsed.returns)
    if parsed.skip is not None:
        records = records[parsed.skip:]
    if parsed.limit is not None:
        records = records[:parsed.limit]
    return records


def _sortable_value(value):
    return (value is None, value)


def _distinct_records(records: list[dict[str, object]], columns: tuple[str, ...]) -> list[dict[str, object]]:
    seen = set()
    distinct = []
    for record in records:
        key = tuple(_hashable_value(record.get(column)) for column in columns)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(record)
    return distinct


def _hashable_value(value):
    if isinstance(value, list):
        return tuple(_hashable_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable_value(item)) for key, item in value.items()))
    return value


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
        return left_id == right_id
    return left == right
