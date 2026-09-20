"""Write support for the GestaltDB Cypher engine: ``CREATE``/``SET``/``REMOVE``.

Writes execute per input row through :class:`WriteBatch`, which collects
node and edge puts from one clause and applies them with the bulk ``GraphDB``
APIs (so label, property, and adjacency indexes stay maintained). Atomicity
is backend-aware: queries run inside :meth:`GraphDB.transaction` when the
backend supports it, and fall back to documented best-effort direct writes
otherwise (LevelDB, non-transactional PyRex, and test doubles).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast import (
    CreateConstraint,
    DropConstraint,
    NodePattern,
    PathPatternClause,
    PatternHop,
    RemoveLabels,
    RemoveProperty,
    SetLabels,
    SetMerge,
    SetProperty,
    SetReplace,
    ShowIndexes,
    ShowConstraints,
)
from .expr import PathValue, _cypher_equals, evaluate_expression
from .runtime import (
    BindingRow,
    _execute_staged,
    _is_node,
    _reset_used_relationships,
    apply_path_pattern_clause,
)
from ...graphdb import Edge, Node
from .temporal import TEMPORAL_TYPES


@dataclass
class WriteBatch:
    """Node/edge puts and deletes for one clause, applied atomically per query."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    deleted_nodes: list[bytes] = field(default_factory=list)
    deleted_edges: list[bytes] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        """Stage a node put."""
        _reject_temporal_properties(node.properties)
        self.nodes.append(node)

    def add_edge(self, edge: Edge) -> None:
        """Stage an edge put."""
        _reject_temporal_properties(edge.properties)
        self.edges.append(edge)

    def apply(self, context) -> None:
        """Write staged entities and refresh the query caches.

        Puts go through ``put_nodes``/``put_edges_bulk`` when available so
        secondary indexes and typed adjacency stay maintained exactly like
        object writes, falling back to single-entity puts for minimal
        graph handles. Cached entities are replaced so later clauses in
        the same query read the new versions.
        """
        graph = context.graph
        nodes = list({graph.node_key_to_bytes(node.get_id): node for node in self.nodes}.values())
        edges = list({graph.node_key_to_bytes(edge.get_id): edge for edge in self.edges}.values())
        checked: list[Node] = []
        for node in nodes:
            check_node_constraints(
                graph,
                node.labels,
                node.properties,
                exclude_id=node.get_id,
                against=checked,
            )
            checked.append(node)
        if nodes:
            put_nodes = getattr(graph, "put_nodes", None)
            if put_nodes is not None:
                put_nodes(nodes)
            else:
                for node in nodes:
                    graph.put_node(node)
            for node in nodes:
                context.node_cache[graph.node_key_to_bytes(node.get_id)] = node
        if edges:
            put_edges_bulk = getattr(graph, "put_edges_bulk", None)
            if put_edges_bulk is not None:
                put_edges_bulk(edges)
            else:
                for edge in edges:
                    graph.put_edge(edge)
            for edge in edges:
                context.edge_cache[graph.node_key_to_bytes(edge.get_id)] = edge
        for edge_id in self.deleted_edges:
            graph.delete_edge(edge_id)
            context.edge_cache.pop(graph.node_key_to_bytes(edge_id), None)
        for node_id in self.deleted_nodes:
            graph.delete_node(node_id)
            context.node_cache.pop(graph.node_key_to_bytes(node_id), None)


def _reject_temporal_properties(value: object) -> None:
    """Keep query-only temporal values out of serializer-dependent storage."""
    if isinstance(value, TEMPORAL_TYPES):
        raise TypeError("Cypher temporal values cannot be persisted as graph properties")
    if isinstance(value, dict):
        for item in value.values():
            _reject_temporal_properties(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_temporal_properties(item)


def transaction_supported(graph) -> bool:
    """Probe whether a graph handle supports transactions, without writes."""
    factory = getattr(graph, "transaction", None)
    if factory is None:
        return False
    try:
        with factory():
            pass
    except NotImplementedError:
        return False
    return True


def _snapshot(rows):
    """Materialize upstream rows before writing (snapshot semantics)."""
    return list(rows)


def get_constraints(graph) -> list[dict[str, str]]:
    """Return the node constraint catalog, including test-double fallback."""
    if not hasattr(graph, "node_constraints"):
        graph.node_constraints = []
    return graph.node_constraints


def _constraint_value(constraint, name: str):
    return constraint[name] if isinstance(constraint, dict) else getattr(constraint, name)


def check_node_constraints(graph, labels, properties: dict, exclude_id=None, against=()) -> None:
    """Enforce uniqueness and existence constraints for a staged node put."""
    constraints = get_constraints(graph)
    if not constraints:
        return
    key = graph.node_key_to_bytes
    for constraint in constraints:
        label = _constraint_value(constraint, "label")
        property_name = _constraint_value(constraint, "property")
        kind = _constraint_value(constraint, "kind")
        if label not in labels:
            continue
        if kind == "exists":
            if properties.get(property_name) is None:
                raise ValueError(
                    f"Existence constraint violated: {label} requires non-null property "
                    f"{property_name!r}"
                )
        else:
            if properties.get(property_name) is None:
                continue
            value = properties[property_name]
            for other in against:
                if label in other.labels and _cypher_equals(
                    other.properties.get(property_name), value
                ) is True:
                    raise ValueError(
                        f"Uniqueness constraint violated: {label}.{property_name} must be unique"
                    )
            for node_id in graph.iter_node_ids_by_label(label):
                if exclude_id is not None and key(exclude_id) == node_id:
                    continue
                node = graph.get_node(node_id)
                if node is None:
                    continue
                if property_name in node.properties and _cypher_equals(
                    node.properties[property_name], value
                ) is True:
                    raise ValueError(
                        f"Uniqueness constraint violated: {label}.{property_name} must be unique"
                    )


def execute_ddl(graph, command) -> tuple[tuple[str, ...], list[dict]]:
    """Execute a constraint command, returning ``(columns, records)``."""
    if isinstance(command, ShowIndexes):
        records = [
            {"entityType": entity_type, "properties": [property_name]}
            for entity_type, property_names in (
                ("NODE", getattr(graph, "indexed_node_properties", set())),
                ("RELATIONSHIP", getattr(graph, "indexed_edge_properties", set())),
            )
            for property_name in sorted(property_names)
        ]
        return ("entityType", "properties"), records
    if isinstance(command, ShowConstraints):
        return (
            ("name", "type", "label", "property"),
            [
                {
                    "name": constraint["name"],
                    "type": constraint["kind"].upper(),
                    "label": constraint["label"],
                    "property": constraint["property"],
                }
                for constraint in get_constraints(graph)
            ],
        )
    if isinstance(command, DropConstraint):
        constraints = get_constraints(graph)
        remaining = [item for item in constraints if item["name"] != command.name]
        if len(remaining) == len(constraints):
            raise ValueError(f"No such constraint: {command.name}")
        _store_constraints(graph, remaining)
        return (), []
    name = command.name or f"{command.label}_{command.property_name}_{command.kind}"
    catalog = get_constraints(graph)
    if any(item["name"] == name for item in catalog):
        raise ValueError(f"Constraint already exists: {name}")
    if any(
        item["label"] == command.label
        and item["property"] == command.property_name
        and item["kind"] == command.kind
        for item in catalog
    ):
        raise ValueError(
            f"Constraint already exists for {command.label}.{command.property_name}"
        )
    _validate_existing_nodes(graph, command)
    _store_constraints(
        graph,
        [
            *catalog,
            {
                "name": name,
                "label": command.label,
                "property": command.property_name,
                "kind": command.kind,
            },
        ],
    )
    return (), []


def _store_constraints(graph, constraints: list[dict[str, str]]) -> None:
    setter = getattr(graph, "set_node_constraints", None)
    if setter is not None:
        setter(constraints)
    else:
        graph.node_constraints = [dict(constraint) for constraint in constraints]


def _validate_existing_nodes(graph, command: CreateConstraint) -> None:
    """Reject a new constraint when existing labeled nodes violate it."""
    seen: list[object] = []
    for node_id in graph.iter_node_ids_by_label(command.label):
        node = graph.get_node(node_id)
        if node is None:
            continue
        value = node.properties.get(command.property_name)
        if command.kind == "exists":
            if value is None:
                raise ValueError(
                    f"Existence constraint violated: {command.label} requires non-null "
                    f"property {command.property_name!r}"
                )
            continue
        if value is None:
            continue
        if any(_cypher_equals(value, previous) is True for previous in seen):
            raise ValueError(
                f"Uniqueness constraint violated: {command.label}."
                f"{command.property_name} must be unique"
            )
        seen.append(value)


def _resolve_pattern(pattern: PathPatternClause, bindings: dict, context) -> PathPatternClause:
    """Evaluate computed property values against row bindings.

    ``CREATE``/``MERGE`` maps accept general expressions (variables,
    parameters, function calls); matching and creation both run on the
    resolved plain values.
    """

    def resolved(properties):
        return tuple((name, evaluate_expression(value, bindings, context)) for name, value in properties)

    source = pattern.source
    resolved_source = NodePattern(
        source.variable, source.labels, resolved(source.properties), source.label_expression
    )
    hops = tuple(
        PatternHop(
            hop.rel_var,
            hop.edge_types,
            NodePattern(
                hop.target.variable,
                hop.target.labels,
                resolved(hop.target.properties),
                hop.target.label_expression,
            ),
            hop.direction,
            resolved(hop.properties),
            hop.min_length,
            hop.max_length,
        )
        for hop in pattern.hops
    )
    return PathPatternClause(resolved_source, hops, pattern.name)


def apply_create(rows, step, context):
    """Execute one ``CREATE`` clause, binding created entities per row."""
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        batch = WriteBatch()
        for pattern in step.patterns:
            _create_pattern(bindings, batch, _resolve_pattern(pattern, bindings, context), context)
        batch.apply(context)
        yield row.with_bindings(bindings)


def apply_merge(rows, step, context):
    """Execute one ``MERGE`` clause: match the patterns or create them.

    Each input row matches independently with a fresh isomorphism scope.
    Fully matched rows run ``ON MATCH`` actions; otherwise the whole
    pattern is created (reusing bound variables) and ``ON CREATE`` runs.
    """
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        staged = _reset_used_relationships(iter([row]))
        resolved_patterns = tuple(
            _resolve_pattern(pattern, row.bindings, context) for pattern in step.patterns
        )
        for pattern in resolved_patterns:
            staged = apply_path_pattern_clause(staged, pattern, context, None, None)
        matched = list(staged)
        batch = WriteBatch()
        if matched:
            for match in matched:
                bindings = dict(match.bindings)
                for item in step.on_match:
                    _apply_set_item(bindings, batch, item, context)
                batch.apply(context)
                yield match.with_bindings(bindings)
        else:
            bindings = dict(row.bindings)
            for pattern in resolved_patterns:
                _create_pattern(bindings, batch, pattern, context)
            for item in step.on_create:
                _apply_set_item(bindings, batch, item, context)
            batch.apply(context)
            yield row.with_bindings(bindings)


def apply_foreach(rows, step, context):
    """Execute one ``FOREACH`` loop, passing input rows through unchanged.

    The body plan runs per row and list element for side effects only; the
    loop variable never escapes. Entity bindings refresh afterwards so later
    clauses observe body writes.
    """
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        items = evaluate_expression(step.iterable, row.bindings, context)
        if items is None:
            yield row
            continue
        if not isinstance(items, (list, tuple)):
            raise TypeError("FOREACH expects a list value")
        for item in items:
            seed = row.with_bindings(
                {**row.bindings, step.variable: item},
                preserve_current_node=False,
                current_node_id=None,
                used_relationship_ids=frozenset(),
            )
            _execute_staged(step.plan, context, initial=[seed], require_projection=False)
        yield _refresh_entities(row, context)


def _refresh_entities(row: BindingRow, context) -> BindingRow:
    """Re-read entity bindings so later clauses observe loop writes."""
    bindings = dict(row.bindings)
    for variable, value in bindings.items():
        if _is_node(value) or hasattr(value, "properties"):
            key = context.graph.node_key_to_bytes(value.get_id)
            refreshed = context.get_node(key) if _is_node(value) else context.get_edge(key)
            bindings[variable] = refreshed
    return row.with_bindings(bindings)


def _create_pattern(bindings: dict, batch: WriteBatch, pattern, context) -> None:
    """Create one pattern's nodes and edges, extending ``bindings``."""
    source_id, _ = _create_or_reuse_node(bindings, batch, pattern.source, context)
    node_ids = [source_id]
    edge_ids: list = []
    previous_id = source_id
    for hop in pattern.hops:
        if len(hop.edge_types) != 1 or (hop.min_length, hop.max_length) != (1, 1):
            raise ValueError("CREATE relationships must specify exactly one type and no variable length")
        target_id, _ = _create_or_reuse_node(bindings, batch, hop.target, context)
        edge = _create_edge(bindings, batch, hop, previous_id, target_id, context)
        node_ids.append(target_id)
        edge_ids.append(edge.get_id)
        previous_id = target_id
    if pattern.name is not None:
        nodes = [context.get_node(context.node_key_to_bytes(node_id)) for node_id in node_ids]
        edges = [context.get_edge(context.node_key_to_bytes(edge_id)) for edge_id in edge_ids]
        bindings[pattern.name] = PathValue(tuple(nodes), tuple(edges))


def _create_or_reuse_node(bindings: dict, batch: WriteBatch, node_pattern, context):
    """Create or reuse one ``CREATE`` node pattern, returning its ID and node."""
    variable = node_pattern.variable
    properties = {name: context.resolve(value) for name, value in node_pattern.properties}
    node_id = properties.pop("id", None)
    node_id = None if node_id is None else str(node_id)
    bound = bindings.get(variable) if variable is not None else None
    if bound is not None:
        if not _is_node(bound):
            raise TypeError(f"CREATE reuses variable {variable} as a node, got {type(bound).__name__}")
        if node_id is not None and node_id != bound.get_id:
            raise ValueError(f"CREATE node id {node_id!r} conflicts with bound variable {variable}")
        merged = Node(
            node_id=bound.get_id,
            properties={**bound.properties, **properties},
            labels=[*bound.labels, *node_pattern.labels],
        )
        batch.add_node(merged)
        if variable is not None:
            bindings[variable] = merged
        return merged.get_id, merged
    node = Node(node_id=node_id, properties=properties, labels=list(node_pattern.labels))
    batch.add_node(node)
    if variable is not None:
        bindings[variable] = node
    return node.get_id, node


def _create_edge(bindings: dict, batch: WriteBatch, hop, source_id: str, target_id: str, context):
    """Create one ``CREATE`` relationship, returning the edge."""
    rel_var = hop.rel_var
    bound = bindings.get(rel_var) if rel_var is not None else None
    if bound is not None:
        raise ValueError(f"Variable {rel_var} is already defined")
    properties = {name: context.resolve(value) for name, value in hop.properties}
    edge_id = properties.pop("id", None)
    edge_id = None if edge_id is None else str(edge_id)
    declared_type = properties.pop("type", None)
    edge_type = hop.edge_types[0]
    if declared_type is not None and declared_type != edge_type:
        raise ValueError(
            f"CREATE relationship type {edge_type!r} conflicts with property 'type' {declared_type!r}"
        )
    edge = Edge(
        edge_id=edge_id,
        source=source_id,
        target=target_id,
        properties={"type": edge_type, **properties},
    )
    batch.add_edge(edge)
    if rel_var is not None:
        bindings[rel_var] = edge
    return edge


def apply_set(rows, step, context):
    """Execute one ``SET`` clause with copy-on-write per row."""
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        batch = WriteBatch()
        for item in step.items:
            _apply_set_item(bindings, batch, item, context)
        batch.apply(context)
        yield row.with_bindings(bindings)


def _apply_set_item(bindings: dict, batch: WriteBatch, item, context) -> None:
    """Apply one ``SET`` item; ``None`` targets are a null-safe no-op."""
    target = bindings.get(item.variable)
    if target is None:
        return
    if isinstance(item, SetProperty):
        value = evaluate_expression(item.expression, bindings, context)
        _store_entity(batch, bindings, item.variable, _with_properties(target, {item.property_name: value}))
    elif isinstance(item, SetLabels):
        _require_node(target, item.variable, "SET labels")
        _store_entity(batch, bindings, item.variable, _with_labels(target, item.labels, add=True))
    elif isinstance(item, SetMerge):
        value = evaluate_expression(item.expression, bindings, context)
        if value is None:
            return
        if not isinstance(value, dict):
            raise TypeError(f"SET += expects a map for {item.variable}")
        _store_entity(batch, bindings, item.variable, _with_properties(target, value))
    elif isinstance(item, SetReplace):
        value = evaluate_expression(item.expression, bindings, context)
        if value is None:
            return
        if not isinstance(value, dict):
            raise TypeError(f"SET = expects a map for {item.variable}")
        _store_entity(batch, bindings, item.variable, _replace_properties(target, value))
    else:  # pragma: no cover - planner invariant
        raise TypeError(f"Unsupported SET item: {type(item).__name__}")


def apply_remove(rows, step, context):
    """Execute one ``REMOVE`` clause with copy-on-write per row."""
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        batch = WriteBatch()
        for item in step.items:
            _apply_remove_item(bindings, batch, item, context)
        batch.apply(context)
        yield row.with_bindings(bindings)


def apply_delete(rows, step, context):
    """Execute one ``DELETE`` clause, checking relationships unless detaching."""
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        batch = WriteBatch()
        entities = []
        for expression in step.expressions:
            value = evaluate_expression(expression, bindings, context)
            if value is None:
                continue
            if not _is_node(value) and not hasattr(value, "properties"):
                raise TypeError(
                    f"DELETE expects a node or relationship, got {type(value).__name__}"
                )
            entities.append(value)
        deleted_edge_ids: set[bytes] = set()
        for entity in entities:
            if _is_node(entity):
                continue
            if not hasattr(entity, "source") or not hasattr(entity, "get_id"):
                raise TypeError(
                    f"DELETE expects a node or relationship, got {type(entity).__name__}"
                )
            edge_id = context.graph.node_key_to_bytes(entity.get_id)
            deleted_edge_ids.add(edge_id)
            batch.deleted_edges.append(edge_id)
        for entity in entities:
            if not _is_node(entity):
                continue
            node_id = context.graph.node_key_to_bytes(entity.get_id)
            if not step.detach and _has_live_incident_edges(context, node_id, deleted_edge_ids):
                raise ValueError(
                    f"Cannot delete node {entity.get_id!r} with relationships; use DETACH DELETE"
                )
            batch.deleted_nodes.append(node_id)
        batch.apply(context)
        yield row.with_bindings(bindings)


def _has_live_incident_edges(context, node_id: bytes, deleted_edge_ids: set[bytes]) -> bool:
    """Return whether stored edges beyond deleted ones touch a node."""
    for edge_key in _iter_edge_keys(context.graph):
        if edge_key in deleted_edge_ids:
            continue
        edge = context.get_edge(edge_key)
        if edge is None:
            continue
        if (
            context.graph.node_key_to_bytes(edge.source) == node_id
            or context.graph.node_key_to_bytes(edge.target) == node_id
        ):
            return True
    return False


def _iter_edge_keys(graph):
    """Yield stored edge keys across graph handle shapes."""
    generator = getattr(graph, "get_edge_keys_generator", None)
    if generator is not None:
        yield from generator()
        return
    store = getattr(graph, "store", None)
    if store is not None:
        yield from store.get_edge_keys_generator()


def _apply_remove_item(bindings: dict, batch: WriteBatch, item, context) -> None:
    """Apply one ``REMOVE`` item; ``None`` targets are a null-safe no-op."""
    target = bindings.get(item.variable)
    if target is None:
        return
    if isinstance(item, RemoveProperty):
        properties = _entity_properties(target, item.variable, "REMOVE")
        if item.property_name not in properties:
            return
        updated = {name: value for name, value in properties.items() if name != item.property_name}
        _store_entity(batch, bindings, item.variable, _replace_properties(target, updated))
    elif isinstance(item, RemoveLabels):
        _require_node(target, item.variable, "REMOVE labels")
        _store_entity(batch, bindings, item.variable, _with_labels(target, item.labels, add=False))
    else:  # pragma: no cover - planner invariant
        raise TypeError(f"Unsupported REMOVE item: {type(item).__name__}")


def _entity_properties(target, variable: str, clause: str) -> dict:
    """Return a copy of an entity's or map's properties."""
    if isinstance(target, dict):
        return dict(target)
    if _is_node(target) or hasattr(target, "properties"):
        properties = getattr(target, "properties", None)
        if isinstance(properties, dict):
            return dict(properties)
    raise TypeError(f"{clause} expects a map, node, or relationship for {variable}")


def _with_properties(target, updates: dict):
    """Return a copy of an entity or map with merged properties."""
    if isinstance(target, dict):
        merged = dict(target)
        merged.update(updates)
        return merged
    if _is_node(target):
        return Node(node_id=target.get_id, properties={**target.properties, **updates}, labels=list(target.labels))
    if hasattr(target, "properties"):
        return Edge(
            edge_id=target.get_id,
            source=target.source,
            target=target.target,
            properties={**target.properties, **updates},
        )
    raise TypeError(f"SET expects a map, node, or relationship, got {type(target).__name__}")


def _replace_properties(target, properties: dict):
    """Return a copy of an entity or map with replaced properties."""
    if isinstance(target, dict):
        return dict(properties)
    if _is_node(target):
        return Node(node_id=target.get_id, properties=dict(properties), labels=list(target.labels))
    if hasattr(target, "properties"):
        return Edge(
            edge_id=target.get_id,
            source=target.source,
            target=target.target,
            properties=dict(properties),
        )
    raise TypeError(f"SET expects a map, node, or relationship, got {type(target).__name__}")


def _with_labels(target, labels, *, add: bool):
    """Return a node copy with labels added or removed (target pre-validated)."""
    current = list(target.labels)
    if add:
        for label in labels:
            if label not in current:
                current.append(label)
    else:
        current = [label for label in current if label not in labels]
    return Node(node_id=target.get_id, properties=dict(target.properties), labels=current)


def _require_node(target, variable: str, clause: str) -> None:
    """Raise a ``TypeError`` unless a target is a node."""
    if not _is_node(target):
        where = f" for {variable}" if variable else ""
        raise TypeError(f"{clause} requires a node variable{where}")


def _store_entity(batch: WriteBatch, bindings: dict, variable: str, value) -> None:
    """Rebind an updated value, staging stored entities for writing."""
    bindings[variable] = value
    if _is_node(value) or hasattr(value, "properties"):
        if _is_node(value):
            batch.add_node(value)
        else:
            batch.add_edge(value)
