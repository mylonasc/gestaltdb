"""Consumer-side contracts for Cypher streams, caches and structural bindings."""

from collections.abc import Iterator

from typing_extensions import assert_type

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.query_engine.cypher._runtime_types import EdgeSeek, ExactSeek, GraphWriteScope, NodeShape, RangeSeek
from gestaltdb.query_engine.cypher.ast import NodePattern, PathPatternClause
from gestaltdb.query_engine.cypher.plan import CreateStep, DeleteStep, ForeachStep, MergeStep, RemoveStep, SetStep
from gestaltdb.query_engine.cypher.runtime import (
    BindingRow, ProjectedRow, ProjectOperator, QueryContext, _edge_seek_spec,
    _is_node, _variable_paths, cypher_value_key,
)
from gestaltdb.query_engine.cypher.ast import PatternHop
from gestaltdb.query_engine.cypher.scope import GraphQueryScope
from gestaltdb.query_engine.cypher.write import (
    WriteBatch, _with_labels, apply_create, apply_delete, apply_foreach,
    apply_merge, apply_remove, apply_set,
)
from gestaltdb.versioning import EdgeVersion, NodeVersion


def graph_contracts(graph: GraphDB) -> None:
    read: GraphQueryScope = graph
    write: GraphWriteScope = graph
    assert_type(QueryContext(read).get_node(b"n"), Node | None)
    assert_type(write.put_node(Node("n")), None)
    read.put_node(Node("n"))  # type: ignore[attr-defined]


def context_contracts(context: QueryContext) -> None:
    assert_type(context.get_node(b"n"), Node | None)
    assert_type(context.get_edge(b"e"), Edge | None)
    assert_type(context.resolve(42), object)
    assert_type(context.version_for(Node("n")), NodeVersion | EdgeVersion | None)
    assert_type(context.iter_temporal_node_ids(), Iterator[bytes])
    assert_type(context.iter_temporal_adjacency(b"n", ("R",), "out"), Iterator[dict[str, bytes]])
    context.node_cache[b"n"] = "not a node"  # type: ignore[assignment]
    context.get_node("n")  # type: ignore[arg-type]


def structural_contracts(value: object) -> None:
    if _is_node(value):
        assert_type(value, NodeShape)
        assert_type(value.get_id, str)
        assert_type(_with_labels(value, ("L",), add=True), Node)
    assert_type(cypher_value_key(value), tuple[object, ...])


def stream_contracts(context: QueryContext, hop: PatternHop) -> None:
    rows = [BindingRow({"value": 1})]
    assert_type(ProjectOperator(("value",)).execute(rows, context), Iterator[ProjectedRow])
    assert_type(_variable_paths(context, rows[0], hop), Iterator[BindingRow])
    pattern = PathPatternClause(NodePattern("n"), ())
    assert_type(apply_create(rows, CreateStep((pattern,)), context), Iterator[BindingRow])
    assert_type(apply_merge(rows, MergeStep((pattern,)), context), Iterator[BindingRow])
    assert_type(apply_set(rows, SetStep(()), context), Iterator[BindingRow])
    assert_type(apply_remove(rows, RemoveStep(()), context), Iterator[BindingRow])
    assert_type(apply_delete(rows, DeleteStep(()), context), Iterator[BindingRow])
    apply_set(rows, CreateStep((pattern,)), context)  # type: ignore[arg-type]
    WriteBatch().add_node(Edge("e", "a", "b"))  # type: ignore[arg-type]


def foreach_contract(context: QueryContext, step: ForeachStep) -> None:
    assert_type(apply_foreach([BindingRow({})], step, context), Iterator[BindingRow])


def seek_contracts(context: QueryContext) -> None:
    spec = _edge_seek_spec("r", (("score", 1),), None, context)
    assert_type(spec, EdgeSeek | None)
    if spec is not None:
        if spec[0] == "exact":
            assert_type(spec, ExactSeek)
        else:
            assert_type(spec, RangeSeek)
