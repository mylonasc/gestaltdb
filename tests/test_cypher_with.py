from __future__ import annotations

import pytest

from gestaltdb.cypher import execute, parse_ast, plan
from gestaltdb.cypher_ast import WithClause
from gestaltdb.cypher_parser import CypherSemanticError, parse
from gestaltdb.cypher_plan import MatchStep, ProjectItems
from gestaltdb.graphdb import Edge, Node
from tests.test_cypher import FakeCypherGraph


def _people_graph():
    graph = FakeCypherGraph()
    graph.put_node(
        Node(
            node_id="ann",
            labels=["Person"],
            properties={"name": "Ann", "age": 34, "dept": "eng", "active": True},
        )
    )
    graph.put_node(
        Node(
            node_id="bo",
            labels=["Person"],
            properties={"name": "Bo", "age": 41, "dept": "ops", "active": False},
        )
    )
    graph.put_node(Node(node_id="core", labels=["Team"], properties={"name": "Core"}))
    graph.put_edge(Edge(edge_id="e1", source="ann", target="core", properties={"type": "MEMBER_OF"}))
    graph.put_edge(Edge(edge_id="e2", source="bo", target="core", properties={"type": "MEMBER_OF"}))
    return graph


def test_parse_ast_preserves_with_clause_order_and_modifiers():
    query = (
        "MATCH (p:Person) WHERE p.active = true "
        "WITH p.dept AS dept, p ORDER BY dept SKIP 0 LIMIT 10 "
        "MATCH (p)-[:MEMBER_OF]->(t) RETURN dept, t.name AS team"
    )

    parsed = parse_ast(query)

    assert [type(clause).__name__ for clause in parsed.clauses] == [
        "MatchClause",
        "WhereClause",
        "WithClause",
        "MatchClause",
        "ReturnClause",
    ]
    with_clause = parsed.clauses[2]
    assert isinstance(with_clause, WithClause)
    assert [item.alias for item in with_clause.items] == ["dept", None]
    assert with_clause.skip == 0
    assert with_clause.limit == 10


def test_legacy_parse_rejects_with_queries():
    with pytest.raises(CypherSemanticError, match="use parse_ast"):
        parse("MATCH (n) WITH n AS m RETURN m")


def test_with_alias_replaces_original_name_downstream():
    result = execute(_people_graph(), "MATCH (p:Person) WITH p AS person RETURN person.name AS name ORDER BY name")

    assert result.records == [{"name": "Ann"}, {"name": "Bo"}]
    with pytest.raises(CypherSemanticError, match="unbound variable"):
        parse_ast("MATCH (p:Person) WITH p AS person RETURN p.name")


def test_with_star_carries_current_scope_only():
    result = execute(_people_graph(), "MATCH (p:Person) MATCH (t:Team) WITH p RETURN *")

    assert result.columns == ("p",)
    assert len(result.records) == 2


def test_multiple_with_stages_execute_in_source_order():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p AS person WITH person.name AS name RETURN name ORDER BY name",
    )

    assert result.records == [{"name": "Ann"}, {"name": "Bo"}]


def test_where_after_with_observes_with_scope():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p.dept AS dept, p.age AS age WHERE age >= 35 RETURN dept ORDER BY dept",
    )

    assert result.records == [{"dept": "ops"}]


def test_dropped_variables_are_unbound_downstream():
    with pytest.raises(CypherSemanticError, match="unbound variable"):
        parse_ast("MATCH (p:Person) WITH p.name AS name RETURN p.age")


def test_duplicate_names_are_rejected_per_with_clause():
    with pytest.raises(CypherSemanticError, match="duplicate column names"):
        parse_ast("MATCH (p:Person) WITH p.name AS name, p.age AS name RETURN name")


def test_with_modifiers_execute_before_downstream_clauses():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p ORDER BY p.age DESC LIMIT 1 "
        "MATCH (p)-[:MEMBER_OF]->(t:Team) RETURN p.name AS name, t.name AS team",
    )

    assert result.records == [{"name": "Bo", "team": "Core"}]


def test_with_distinct_and_skip():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH DISTINCT p.dept AS dept ORDER BY dept SKIP 1 RETURN dept",
    )

    assert result.records == [{"dept": "ops"}]


def test_parameters_flow_across_scope_boundaries():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p, $minimum AS minimum WHERE p.age >= minimum RETURN p.name AS name",
        parameters={"minimum": 40},
    )

    assert result.records == [{"name": "Bo"}]


def test_downstream_match_correlates_on_retained_variable():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p MATCH (p)-[:MEMBER_OF]->(t) RETURN p.name AS name, t.name AS team ORDER BY name",
    )

    assert result.records == [
        {"name": "Ann", "team": "Core"},
        {"name": "Bo", "team": "Core"},
    ]


def test_relationship_scope_resets_between_match_stages():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e", source="a", target="b", properties={"type": "T"}))

    result = execute(
        graph,
        "MATCH (a)-[r:T]->(b) WITH a, b, r MATCH (a)-[s:T]->(b) RETURN r.id AS first, s.id AS second",
    )

    assert result.records == [{"first": "e", "second": "e"}]


def test_with_out_of_scope_error_points_at_reference():
    query = "MATCH (p:Person)\nWITH p.name AS name\nRETURN p.age"

    with pytest.raises(CypherSemanticError) as caught:
        parse_ast(query)

    assert "unbound variable" in str(caught.value)
    assert caught.value.offset == query.index("p.age")
    assert caught.value.line == 3


def test_staged_plan_has_explicit_scope_boundaries():
    staged = plan("MATCH (p:Person) WITH p AS person RETURN person.name AS name")

    assert staged.staged is True
    assert isinstance(staged.operators[0], MatchStep)
    projections = [operator for operator in staged.operators if isinstance(operator, ProjectItems)]
    assert len(projections) == 2
    assert projections[0].returns == ("person",)
    assert projections[1].returns == ("name",)
    assert staged.columns == ("name",)


def test_chained_match_where_with_match():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WHERE p.active = true WITH p "
        "MATCH (t:Team) MATCH (p)-[:MEMBER_OF]->(t) RETURN p.name AS name, t.name AS team",
    )

    assert result.records == [{"name": "Ann", "team": "Core"}]


def test_with_expression_projections_and_grouped_ordering():
    result = execute(
        _people_graph(),
        "MATCH (p:Person) WITH p.dept AS dept, p.age + 1 AS next RETURN dept, next ORDER BY next",
    )

    assert result.records == [
        {"dept": "eng", "next": 35},
        {"dept": "ops", "next": 42},
    ]
