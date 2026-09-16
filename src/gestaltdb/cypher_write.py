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

from .cypher_ast import (
    RemoveLabels,
    RemoveProperty,
    SetLabels,
    SetMerge,
    SetProperty,
    SetReplace,
)
from .cypher_expr import PathValue, evaluate_expression
from .cypher_runtime import BindingRow, _is_node, _reset_used_relationships, apply_path_pattern_clause
from .graphdb import Edge, Node


@dataclass
class WriteBatch:
    """Node/edge puts and deletes for one clause, applied atomically per query."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    deleted_nodes: list[bytes] = field(default_factory=list)
    deleted_edges: list[bytes] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        """Stage a node put."""
        self.nodes.append(node)

    def add_edge(self, edge: Edge) -> None:
        """Stage an edge put."""
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
        if self.nodes:
            put_nodes = getattr(graph, "put_nodes", None)
            if put_nodes is not None:
                put_nodes(self.nodes)
            else:
                for node in self.nodes:
                    graph.put_node(node)
            for node in self.nodes:
                context.node_cache[graph.node_key_to_bytes(node.get_id)] = node
        if self.edges:
            put_edges_bulk = getattr(graph, "put_edges_bulk", None)
            if put_edges_bulk is not None:
                put_edges_bulk(self.edges)
            else:
                for edge in self.edges:
                    graph.put_edge(edge)
            for edge in self.edges:
                context.edge_cache[graph.node_key_to_bytes(edge.get_id)] = edge
        for edge_id in self.deleted_edges:
            graph.delete_edge(edge_id)
            context.edge_cache.pop(graph.node_key_to_bytes(edge_id), None)
        for node_id in self.deleted_nodes:
            graph.delete_node(node_id)
            context.node_cache.pop(graph.node_key_to_bytes(node_id), None)


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


def apply_create(rows, step, context):
    """Execute one ``CREATE`` clause, binding created entities per row."""
    for row in _snapshot(rows):
        row = BindingRow.from_row(row)
        bindings = dict(row.bindings)
        batch = WriteBatch()
        for pattern in step.patterns:
            _create_pattern(bindings, batch, pattern, context)
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
        for pattern in step.patterns:
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
            for pattern in step.patterns:
                _create_pattern(bindings, batch, pattern, context)
            for item in step.on_create:
                _apply_set_item(bindings, batch, item, context)
            batch.apply(context)
            yield row.with_bindings(bindings)


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
