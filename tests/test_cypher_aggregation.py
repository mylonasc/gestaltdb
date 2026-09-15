from __future__ import annotations

import pytest

from gestaltdb.cypher import execute, parse_ast, plan
from gestaltdb.cypher_parser import CypherSemanticError, parse
from gestaltdb.cypher_plan import Aggregate
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _scores_graph():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"dept": "eng", "age": 30}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"dept": "eng", "age": 40}))
    graph.put_node(Node(node_id="c", labels=["Person"], properties={"dept": "ops", "age": 40}))
    graph.put_node(Node(node_id="d", labels=["Person"], properties={"dept": "ops"}))
    return graph


def test_global_count_star_counts_every_row():
    assert execute(_scores_graph(), "MATCH (n:Person) RETURN count(*)").records == [{"count(*)": 4}]


def test_count_expression_excludes_nulls():
    assert execute(_scores_graph(), "MATCH (n:Person) RETURN count(n.age) AS total").records == [
        {"total": 3}
    ]


def test_all_core_aggregates_on_one_projection():
    result = execute(
        _scores_graph(),
        "MATCH (n:Person) RETURN collect(n.age) AS ages, sum(n.age) AS total, "
        "avg(n.age) AS average, min(n.age) AS lo, max(n.age) AS hi",
    )

    assert result.records == [
        {"ages": [30, 40, 40], "total": 110, "average": pytest.approx(110 / 3), "lo": 30, "hi": 40}
    ]


def test_empty_input_global_aggregation_uses_documented_defaults():
    result = execute(
        _scores_graph(),
        "MATCH (n:Missing) RETURN count(*), count(n.id), collect(n.id), sum(n.age), avg(n.age), min(n.age), max(n.age)",
    )

    assert result.records == [
        {
            "count(*)": 0,
            "count(n.id)": 0,
            "collect(n.id)": [],
            "sum(n.age)": 0,
            "avg(n.age)": None,
            "min(n.age)": None,
            "max(n.age)": None,
        }
    ]


def test_grouped_aggregation_with_first_seen_order():
    result = execute(
        _scores_graph(), "MATCH (n:Person) RETURN n.dept AS dept, count(*) AS total ORDER BY dept"
    )

    assert result.records == [{"dept": "eng", "total": 2}, {"dept": "ops", "total": 2}]


def test_grouped_empty_input_returns_no_rows():
    assert execute(_scores_graph(), "MATCH (n:Missing) RETURN n.dept, count(*)").records == []


def test_null_grouping_key_forms_one_group():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"kind": None}))
    graph.put_node(Node(node_id="b"))

    result = execute(graph, "MATCH (n) RETURN n.kind AS kind, count(*) AS total")

    assert result.records == [{"kind": None, "total": 2}]


def test_aggregate_distinct_deduplicates_before_accumulation():
    result = execute(
        _scores_graph(),
        "MATCH (n:Person) RETURN count(DISTINCT n.age) AS ages, collect(DISTINCT n.age) AS seen ORDER BY ages",
    )

    assert result.records == [{"ages": 2, "seen": [30, 40]}]


def test_multiple_grouping_keys_and_aggregates():
    result = execute(
        _scores_graph(),
        "MATCH (n:Person) RETURN n.dept AS dept, n.age AS age, count(*) AS total ORDER BY dept, age",
    )

    assert result.records == [
        {"dept": "eng", "age": 30, "total": 1},
        {"dept": "eng", "age": 40, "total": 1},
        {"dept": "ops", "age": 40, "total": 1},
        {"dept": "ops", "age": None, "total": 1},
    ]


def test_aggregation_in_with_with_post_aggregation_filter():
    result = execute(
        _scores_graph(),
        "MATCH (n:Person) WITH n.dept AS dept, count(*) AS total WHERE total > 1 RETURN dept ORDER BY dept",
    )

    assert result.records == [{"dept": "eng"}, {"dept": "ops"}]


def test_aggregate_alias_ordering_and_pagination():
    result = execute(
        _scores_graph(),
        "MATCH (n:Person) RETURN n.dept AS dept, count(*) AS total ORDER BY total DESC, dept SKIP 1 LIMIT 1",
    )

    assert result.records == [{"dept": "ops", "total": 2}]


def test_order_by_aggregate_expression_resolves_to_output():
    result = execute(
        _scores_graph(), "MATCH (n:Person) RETURN n.dept AS dept, count(*) ORDER BY count(*) DESC, dept"
    )

    assert [record["dept"] for record in result.records] == ["eng", "ops"]


def test_return_distinct_applies_after_aggregation():
    result = execute(_scores_graph(), "MATCH (n:Person) RETURN DISTINCT n.dept AS dept ORDER BY dept")

    assert result.records == [{"dept": "eng"}, {"dept": "ops"}]


def test_true_and_one_are_distinct_aggregate_values():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"value": True}))
    graph.put_node(Node(node_id="b", properties={"value": 1}))

    result = execute(graph, "MATCH (n) RETURN count(DISTINCT n.value) AS total, collect(DISTINCT n.value) AS seen")

    assert result.records[0]["total"] == 2
    assert sorted(result.records[0]["seen"], key=str) == sorted([True, 1], key=str)


def test_entity_grouping_keys_use_stable_identity():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"]))
    graph.put_node(Node(node_id="b", labels=["Person"]))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="a", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (a)-[r:T]->(b) RETURN a, count(*) AS total")

    assert len(result.records) == 1
    assert result.records[0]["total"] == 2


def test_sum_and_avg_reject_non_numeric_values():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"value": "oops"}))

    with pytest.raises(TypeError, match="numeric operands"):
        execute(graph, "MATCH (n) RETURN sum(n.value)")
    with pytest.raises(TypeError, match="numeric operands"):
        execute(graph, "MATCH (n) RETURN avg(n.value)")


def test_sum_rejects_boolean_values():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"value": True}))

    with pytest.raises(TypeError, match="numeric operands"):
        execute(graph, "MATCH (n) RETURN sum(n.value)")


def test_min_max_reject_incompatible_values():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"value": 1}))
    graph.put_node(Node(node_id="b", properties={"value": "oops"}))

    with pytest.raises(TypeError, match="comparable values"):
        execute(graph, "MATCH (n) RETURN min(n.value)")
    with pytest.raises(TypeError, match="comparable values"):
        execute(graph, "MATCH (n) RETURN max(n.value)")


def test_collect_order_follows_first_seen_input_order():
    result = execute(_scores_graph(), "MATCH (n:Person) RETURN collect(n.age) AS ages")

    assert result.records == [{"ages": [30, 40, 40]}]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("MATCH (n) RETURN count(*, n.id)", "Unsupported Cypher query"),
        ("MATCH (n) RETURN nofunc(n.id)", "Unsupported function: nofunc"),
        ("MATCH (n) RETURN count(sum(n.id))", "Nested aggregate"),
        ("MATCH (n) WHERE count(n.id) > 1 RETURN n", "Aggregates cannot be used in WHERE"),
        ("MATCH (n) RETURN count(DISTINCT *)", "Unsupported Cypher query"),
        ("MATCH (n) RETURN count(*) + 1", "top-level projection"),
        ("MATCH (n) RETURN sum(*)", "not supported"),
        ("MATCH (n) RETURN n.id ORDER BY count(n.id)", "aggregate projection"),
        ("MATCH (n) RETURN count(n.id) AS total, n.id AS total", "duplicate column names"),
    ],
)
def test_invalid_aggregates_are_rejected(query, message):
    with pytest.raises(ValueError, match=message):
        parse_ast(query)


def test_legacy_parse_rejects_aggregate_queries():
    with pytest.raises(CypherSemanticError, match="use parse_ast"):
        parse("MATCH (n) RETURN count(*)")


def test_aggregate_plan_contains_explicit_grouping_operator():
    staged = plan("MATCH (n:Person) RETURN n.dept AS dept, count(*) AS total")

    aggregates = [operator for operator in staged.operators if isinstance(operator, Aggregate)]

    assert len(aggregates) == 1
    assert aggregates[0].returns == ("dept", "total")
    assert aggregates[0].keys[0][0] == "dept"
    assert aggregates[0].calls[0][0] == "total"
    assert aggregates[0].calls[0][1].function == "count"
    assert aggregates[0].calls[0][1].argument is None
    assert staged.columns == ("dept", "total")


def test_case_insensitive_aggregate_names():
    assert execute(_scores_graph(), "MATCH (n:Person) RETURN COUNT(*)").records == [{"count(*)": 4}]


def test_aggregation_over_with_outputs():
    result = execute(_scores_graph(), "MATCH (n:Person) WITH n AS person RETURN count(person) AS total")

    assert result.records == [{"total": 4}]
