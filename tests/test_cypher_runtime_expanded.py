import pytest

from gestaltdb.cypher import execute, parse, plan
from gestaltdb.query_engine.cypher.ast import Parameter
from gestaltdb.query_engine.cypher.plan import (
    Distinct,
    Limit,
    MatchStep,
    Skip,
    Sort,
)
from gestaltdb.graphdb import Edge, Node

from .test_cypher import FakeCypherGraph


def _people_graph():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="ada", labels=["Person"], properties={"age": 36, "minimum": 21, "name": "Ada Lovelace", "active": True}))
    graph.put_node(Node(node_id="grace", labels=["Person"], properties={"age": 20, "minimum": 21, "name": "Grace Hopper", "active": False}))
    graph.put_node(Node(node_id="unknown", labels=["Person"], properties={}))
    return graph


def test_boolean_arithmetic_and_expression_comparisons_execute():
    graph = _people_graph()

    result = execute(
        graph,
        "MATCH (n:Person) WHERE (n.age + 1 >= n.minimum * 1 AND NOT n.active = false) OR n.name = 'nobody' RETURN n.id",
    )

    assert result.records == [{"n.id": "ada"}]


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("n.name STARTS WITH 'Ada'", "ada"),
        ("n.name ENDS WITH 'Hopper'", "grace"),
        ("n.name CONTAINS 'Love'", "ada"),
        ("n.name =~ 'Grace .*'", "grace"),
    ],
)
def test_string_predicates_execute(predicate, expected):
    result = execute(_people_graph(), f"MATCH (n:Person) WHERE {predicate} RETURN n.id")

    assert result.records == [{"n.id": expected}]


def test_null_comparisons_use_three_valued_logic():
    graph = _people_graph()

    assert execute(graph, "MATCH (n:Person) WHERE n.missing = null RETURN n.id").records == []
    assert execute(graph, "MATCH (n:Person) WHERE n.missing <> 1 RETURN n.id").records == []
    assert execute(graph, "MATCH (n:Person) WHERE n.missing IS NULL RETURN n.id").records == [
        {"n.id": "ada"},
        {"n.id": "grace"},
        {"n.id": "unknown"},
    ]
    assert execute(graph, "MATCH (n:Person) WHERE n.age IN [99, null] RETURN n.id").records == []
    assert execute(graph, "MATCH (n:Person) WHERE NOT n.age IN [99, null] RETURN n.id").records == []


def test_multiple_inline_properties_are_all_applied():
    graph = _people_graph()

    result = execute(graph, "MATCH (n:Person {active: true, age: 36}) RETURN n.id")

    assert result.records == [{"n.id": "ada"}]


def test_parameter_pagination_and_order_by_alias_execute():
    graph = _people_graph()

    result = execute(
        graph,
        "MATCH (n:Person) WHERE n.age IS NOT NULL RETURN n.id AS id, n.age AS age ORDER BY age DESC SKIP $skip LIMIT $limit",
        parameters={"skip": 1, "limit": 1},
    )

    assert result.records == [{"id": "grace", "age": 20}]


@pytest.mark.parametrize("parameters", [{"skip": -1}, {"skip": True}, {"skip": 1.5}])
def test_parameter_pagination_requires_non_negative_integer(parameters):
    with pytest.raises(ValueError, match="SKIP must be a non-negative integer"):
        execute(_people_graph(), "MATCH (n) RETURN n SKIP $skip", parameters=parameters)


def test_multi_hop_limit_is_not_pushed_into_incomplete_branch():
    graph = FakeCypherGraph()
    for node_id in ("start", "dead-end", "live", "result"):
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="start", target="dead-end", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="start", target="live", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e3", source="live", target="result", properties={"type": "T"}))

    result = execute(graph, "MATCH (a {id: 'start'})-[:T]->(b)-[:T]->(c) RETURN c.id LIMIT 1")

    assert result.records == [{"c.id": "result"}]


def test_one_pattern_cannot_reuse_anonymous_relationship():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (a {id: 'a'})-[:T]-(b)-[:T]-(c) RETURN c.id")

    assert result.records == []


def test_logical_plan_represents_all_result_shaping_operators():
    logical_plan = plan("MATCH (n) RETURN DISTINCT n.age ORDER BY n.age SKIP $skip LIMIT 2")

    assert any(isinstance(operator, Distinct) for operator in logical_plan.operators)
    assert any(isinstance(operator, Sort) for operator in logical_plan.operators)
    assert any(isinstance(operator, Skip) and operator.count == Parameter("skip") for operator in logical_plan.operators)
    assert isinstance(logical_plan.operators[-1], Limit)


def test_logical_plan_represents_general_path_filters():
    logical_plan = plan("MATCH (a {id: 'a'})-->(b:Target {active: true}) RETURN b")

    step = logical_plan.operators[0]
    assert isinstance(step, MatchStep)
    assert ("id", "a") in step.patterns[0].source.properties
    assert step.patterns[0].hops[0].target.labels == ("Target",)
    assert ("active", True) in step.patterns[0].hops[0].target.properties


def test_dynamic_comparison_does_not_use_property_index(graph_db):
    graph_db.put_nodes([
        Node(node_id="a", labels=["Person"], properties={"age": 36, "minimum": 21}),
        Node(node_id="b", labels=["Person"], properties={"age": 18, "minimum": 21}),
    ])
    graph_db.create_node_property_index("age")

    result = graph_db.query("MATCH (n:Person) WHERE n.age >= n.minimum RETURN n.id")

    assert result.records == [{"n.id": "a"}]


def test_chained_incoming_relationship_uses_actual_source_direction():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Start"]))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (a:Start) MATCH (b)<-[:T]-(a) RETURN b.id")

    assert result.records == [{"b.id": "b"}]


def test_unanchored_undirected_relationship_emits_both_orientations():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (x)-[:T]-(y) RETURN x.id, y.id ORDER BY x.id")

    assert result.records == [{"x.id": "a", "y.id": "b"}, {"x.id": "b", "y.id": "a"}]


def test_order_by_property_of_entity_alias():
    graph = _people_graph()

    result = execute(graph, "MATCH (n:Person) WHERE n.age IS NOT NULL RETURN n AS person ORDER BY person.age")

    assert [record["person"].get_id for record in result.records] == ["grace", "ada"]


def test_equality_distinguishes_booleans_and_numbers():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="boolean", properties={"value": True}))
    graph.put_node(Node(node_id="number", properties={"value": 1}))

    assert execute(graph, "MATCH (n) WHERE n.value = true RETURN n.id").records == [{"n.id": "boolean"}]
    assert execute(graph, "MATCH (n) WHERE n.value = 1 RETURN n.id").records == [{"n.id": "number"}]


def test_general_path_filters_every_node_and_supports_anonymous_intermediate():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Start"], properties={"active": True}))
    graph.put_node(Node(node_id="middle"))
    graph.put_node(Node(node_id="wanted", labels=["Target"], properties={"rank": 1}))
    graph.put_node(Node(node_id="ignored", labels=["Target"], properties={"rank": 2}))
    graph.put_edge(Edge(edge_id="e1", source="a", target="middle", properties={"type": "A"}))
    graph.put_edge(Edge(edge_id="e2", source="middle", target="wanted", properties={"type": "B"}))
    graph.put_edge(Edge(edge_id="e3", source="middle", target="ignored", properties={"type": "B"}))

    result = execute(
        graph,
        "MATCH (a:Start {active: true})-[:A]->()-[:B]->(target:Target {rank: 1}) RETURN a.id, target.id",
    )

    assert result.records == [{"a.id": "a", "target.id": "wanted"}]


def test_untyped_relationship_matches_typed_and_typeless_edges(graph_db):
    graph_db.put_nodes([Node(node_id="a"), Node(node_id="b"), Node(node_id="c")])
    graph_db.put_edge(Edge(edge_id="typed", source="a", target="b", properties={"type": "T"}))
    graph_db.put_edge(Edge(edge_id="typeless", source="a", target="c"))

    result = graph_db.query("MATCH (a)-[r]->(b) RETURN r.id, b.id ORDER BY r.id")

    assert result.records == [
        {"r.id": "typed", "b.id": "b"},
        {"r.id": "typeless", "b.id": "c"},
    ]
    assert list(graph_db.iter_edge_ids(num_edges=0)) == []


def test_parameterized_id_anchor_combines_with_target_filters():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b", labels=["Target"], properties={"active": True}))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    result = execute(
        graph,
        "MATCH (a {id: $id})-[:T]->(b:Target {active: true}) RETURN b.id",
        parameters={"id": "a"},
    )

    assert result.records == [{"b.id": "b"}]


def test_bracketless_and_incoming_untyped_relationships():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Start"]))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    assert execute(graph, "MATCH (a:Start)-->(b) RETURN b.id").records == [{"b.id": "b"}]
    assert execute(graph, "MATCH (b)<--(a:Start) RETURN b.id").records == [{"b.id": "b"}]


def test_comma_pattern_parts_join_and_form_cartesian_products():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["A"]))
    graph.put_node(Node(node_id="b", labels=["B"]))
    graph.put_node(Node(node_id="c", labels=["C"]))
    graph.put_edge(Edge(edge_id="ab", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="bc", source="b", target="c", properties={"type": "U"}))

    joined = execute(graph, "MATCH (a:A)-[:T]->(b), (b)-[:U]->(c) RETURN c.id")
    product = execute(graph, "MATCH (a:A), (c:C) RETURN a.id, c.id")

    assert joined.records == [{"c.id": "c"}]
    assert product.records == [{"a.id": "a", "c.id": "c"}]


def test_relationship_isomorphism_spans_comma_parts_but_resets_for_match():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["A"]))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    same_clause = execute(graph, "MATCH (a:A)-[r:T]->(b), (a)-[s:T]->(b) RETURN r.id, s.id")
    chained = execute(graph, "MATCH (a:A)-[r:T]->(b) MATCH (a)-[s:T]->(b) RETURN r.id, s.id")

    assert same_clause.records == []
    assert chained.records == [{"r.id": "e", "s.id": "e"}]


def test_untyped_undirected_self_loop_emits_once():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_edge(Edge(edge_id="self", source="a", target="a"))

    result = execute(graph, "MATCH (a)-[r]-(b) RETURN r.id")

    assert result.records == [{"r.id": "self"}]


def test_repeated_endpoint_variable_only_matches_self_loops():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="ordinary", source="a", target="b", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="self", source="b", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (x)-[:T]->(x) RETURN x.id")

    assert result.records == [{"x.id": "b"}]


def test_nested_inline_property_parameters_are_resolved():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", properties={"tags": ["x"], "metadata": {"rank": 1}}))

    result = execute(
        graph,
        "MATCH (n {tags: [$tag], metadata: {rank: $rank}}) RETURN n.id",
        parameters={"tag": "x", "rank": 1},
    )

    assert result.records == [{"n.id": "a"}]


def test_incoming_return_star_preserves_textual_variable_order():
    parsed = parse("MATCH (a)<-[r:T]-(b) RETURN *")

    assert parsed.returns == ("a", "r", "b")
