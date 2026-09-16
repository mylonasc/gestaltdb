from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from gestaltdb.cypher import execute, parse, plan
from gestaltdb.query_engine.cypher.plan import (
    Distinct,
    FilterExpression,
    MatchStep,
    ProcedureSource,
    Sort,
)
from gestaltdb.query_engine.cypher.runtime import (
    BindingRow,
    DistinctOperator,
    LimitOperator,
    ProjectedRow,
    ProjectOperator,
    QueryContext,
    SkipOperator,
    SortOperator,
    cypher_value_key,
    execute_plan,
)
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def test_binding_row_exposes_typed_attributes():
    row = BindingRow(
        bindings={"n": "node"},
        current_node_id=b"n",
        used_relationship_ids=frozenset({b"e"}),
    )

    assert row.bindings == {"n": "node"}
    assert row.current_node_id == b"n"
    assert row.used_relationship_ids == frozenset({b"e"})


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


def test_match_step_binds_relationships_through_staged_plan():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_node(Node(node_id="n2"))
    graph.put_edge(
        Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"})
    )

    result = execute(graph, 'MATCH (a {id: "n1"})-[r:T]->(b) RETURN r.id, b.id')

    assert result.records == [{"r.id": "e1", "b.id": "n2"}]


def test_projected_row_exposes_values_with_optional_source_bindings():
    row = ProjectedRow(values={"name": "Alice"}, source_bindings={"n": "node"})

    assert row.values == {"name": "Alice"}
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


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Person) RETURN n",
        'MATCH (n {id: "n"})-[:T]->(m) RETURN m',
        "MATCH (n)-[:T]->(m) RETURN m",
        "MATCH (n:Person), (m:Person) RETURN n, m",
    ],
)
def test_clause_queries_plan_to_staged_match_steps(query):
    logical_plan = plan(query)

    assert logical_plan.staged is True
    assert any(isinstance(operator, MatchStep) for operator in logical_plan.operators)


def test_procedure_call_plans_to_a_source_backed_plan():
    logical_plan = plan('CALL pg.sample_typed_paths(["n"], []) YIELD path RETURN path')

    assert isinstance(logical_plan.source, ProcedureSource)


def test_execute_obeys_the_generated_logical_plan(monkeypatch):
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="alice", properties={"active": True}))
    graph.put_node(Node(node_id="bob", properties={"active": False}))
    query = "MATCH (n) WHERE n.active = true RETURN n.id ORDER BY n.id"
    logical_plan = plan(query)
    plan_without_filter = replace(
        logical_plan,
        operators=tuple(
            operator
            for operator in logical_plan.operators
            if not isinstance(operator, FilterExpression)
        ),
    )
    monkeypatch.setattr(
        "gestaltdb.query_engine.cypher.api.plan_staged_query", lambda canonical: plan_without_filter
    )

    result = execute(graph, query)

    assert result.records == [{"n.id": "alice"}, {"n.id": "bob"}]


def test_staged_result_operator_order_applies_distinct_before_sort():
    logical_plan = plan("MATCH (n) RETURN DISTINCT n.kind ORDER BY n.kind")
    result_operators = [
        operator
        for operator in logical_plan.operators
        if isinstance(operator, (Sort, Distinct))
    ]

    assert [type(operator) for operator in result_operators] == [Distinct, Sort]


def test_execute_plan_rejects_unknown_logical_operator():
    logical_plan = plan("MATCH (n) RETURN n")
    invalid_plan = replace(
        logical_plan, operators=(object(), *logical_plan.operators)
    )

    with pytest.raises(TypeError, match="Unsupported staged operator: object"):
        execute_plan(invalid_plan, QueryContext(FakeCypherGraph()))


def test_cypher_value_key_separates_booleans_from_numbers():
    assert cypher_value_key(True) != cypher_value_key(1)
    assert cypher_value_key(False) != cypher_value_key(0)
    assert cypher_value_key(1) == cypher_value_key(1.0)


def test_cypher_value_key_identifies_entities_by_kind_and_stable_id():
    first = Node(node_id="n", labels=["Person"])
    second = Node(node_id="n", labels=["Person"])
    edge = Edge(edge_id="n", source="a", target="b", properties={"type": "T"})

    assert cypher_value_key(first) == cypher_value_key(second)
    assert cypher_value_key(first) != cypher_value_key(edge)


def test_cypher_value_key_canonicalizes_nested_values():
    assert cypher_value_key([1, {"b": 2, "a": 1}]) == cypher_value_key([1, {"a": 1, "b": 2}])
    assert cypher_value_key(None) != cypher_value_key(False)


def test_distinct_operator_keeps_true_and_one_as_separate_rows():
    rows = [ProjectedRow({"value": True}), ProjectedRow({"value": 1})]

    result = list(DistinctOperator(("value",)).execute(rows, QueryContext(object())))

    assert result == rows
