from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from gestaltdb.cypher import parse
from gestaltdb.cypher_runtime import (
    BindingRow,
    DistinctOperator,
    LimitOperator,
    ProjectedRow,
    ProjectOperator,
    QueryContext,
    SkipOperator,
    SortOperator,
    expand_typed,
)
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def test_binding_row_preserves_legacy_mapping_access():
    row = BindingRow(
        bindings={"n": "node"},
        current_node_id=b"n",
        used_relationship_ids=frozenset({b"e"}),
    )

    assert row["bindings"] == {"n": "node"}
    assert row.get("current_node_id") == b"n"
    assert dict(row) == {
        "bindings": {"n": "node"},
        "current_node_id": b"n",
        "used_relationship_ids": frozenset({b"e"}),
    }


def test_binding_row_adapts_legacy_dictionary_without_mutating_it():
    legacy = {"current_node_id": b"n", "bindings": {"n": "node"}}

    row = BindingRow.from_row(legacy)
    row.bindings["added"] = "value"

    assert row.used_relationship_ids == frozenset()
    assert legacy == {"current_node_id": b"n", "bindings": {"n": "node"}}


def test_binding_row_traversal_updates_return_new_rows():
    row = BindingRow(bindings={"a": "node-a"}, current_node_id=b"a")

    updated = row.with_bindings(
        {"a": "node-a", "b": "node-b"},
        current_node_id=b"b",
        preserve_current_node=False,
        used_relationship_ids=frozenset({b"edge"}),
    )

    assert row.current_node_id == b"a"
    assert row.used_relationship_ids == frozenset()
    assert updated.current_node_id == b"b"
    assert updated.used_relationship_ids == frozenset({b"edge"})
    with pytest.raises(FrozenInstanceError):
        updated.current_node_id = b"changed"  # type: ignore[misc]


def test_expand_typed_accepts_legacy_rows_and_emits_typed_rows():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_node(Node(node_id="n2"))
    graph.put_edge(
        Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"})
    )
    parsed = parse('MATCH (a {id: "n1"})-[r:T]->(b) RETURN r.id, b.id')
    rows = [{"current_node_id": b"n1", "bindings": {"a": graph.get_node(b"n1")}}]

    expanded = list(expand_typed(QueryContext(graph), rows, parsed.hops[0]))

    assert len(expanded) == 1
    assert isinstance(expanded[0], BindingRow)
    assert expanded[0]["bindings"]["r"].get_id == "e1"
    assert expanded[0]["bindings"]["b"].get_id == "n2"
    assert expanded[0].used_relationship_ids == frozenset({b"e1"})


def test_projected_row_is_a_result_mapping_with_optional_source_bindings():
    row = ProjectedRow(values={"name": "Alice"}, source_bindings={"n": "node"})

    assert dict(row) == {"name": "Alice"}
    assert row.source_bindings == {"n": "node"}


def test_project_operator_retains_source_bindings_for_ordering():
    graph = FakeCypherGraph()
    context = QueryContext(graph)
    node = Node(node_id="n", properties={"name": "Alice", "rank": 2})
    parsed = parse("MATCH (n) RETURN n.name AS name")

    projected = list(
        ProjectOperator(
            parsed.returns,
            parsed.projections,
            parsed.projection_expressions,
        ).execute([BindingRow(bindings={"n": node})], context)
    )

    assert projected == [
        ProjectedRow(values={"name": "Alice"}, source_bindings={"n": node})
    ]


def test_limit_zero_does_not_consume_projected_rows():
    consumed = []

    def rows():
        consumed.append(True)
        yield ProjectedRow({"n": 1})

    result = list(LimitOperator(0).execute(rows(), QueryContext(object())))

    assert result == []
    assert consumed == []


def test_skip_operator_streams_without_materializing_all_rows():
    consumed = []

    def rows():
        for value in range(5):
            consumed.append(value)
            yield ProjectedRow({"n": value})

    skipped = SkipOperator(2).execute(rows(), QueryContext(object()))

    assert next(iter(skipped)).values == {"n": 2}
    assert consumed == [0, 1, 2]


def test_distinct_operator_streams_first_projected_row():
    first = ProjectedRow({"value": [1, 2]}, source_bindings={"row": "first"})
    duplicate = ProjectedRow(
        {"value": [1, 2]}, source_bindings={"row": "duplicate"}
    )

    result = list(
        DistinctOperator(("value",)).execute(
            [first, duplicate], QueryContext(object())
        )
    )

    assert result == [first]


def test_sort_operator_uses_hidden_source_expression_and_is_stable():
    parsed = parse("MATCH (n) RETURN n.name AS name ORDER BY n.rank")
    first = Node(node_id="first", properties={"name": "First", "rank": 2})
    second = Node(node_id="second", properties={"name": "Second", "rank": 1})
    third = Node(node_id="third", properties={"name": "Third", "rank": 2})
    rows = [
        ProjectedRow({"name": node.properties["name"]}, {"n": node})
        for node in (first, second, third)
    ]

    result = list(
        SortOperator(parsed.order_by, parsed).execute(rows, QueryContext(object()))
    )

    assert [row.values["name"] for row in result] == ["Second", "First", "Third"]
