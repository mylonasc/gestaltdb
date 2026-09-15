import pytest

from gestaltdb.cypher import execute, parse_ast
from gestaltdb.cypher_parser import CypherSemanticError, parse
from gestaltdb.cypher_semantics import SymbolKind, analyze_query
from gestaltdb.graphdb import Node
from tests.test_cypher import FakeCypherGraph


def test_analysis_preserves_symbol_order_and_entity_kinds():
    query = parse_ast("MATCH (a)-[r:T]->(b), (b)-[s:U]->(c) RETURN a, r, c")

    analysis = analyze_query(query)
    match_scope = analysis.clauses[0].scope_after

    assert match_scope.names == ("a", "r", "b", "s", "c")
    assert tuple(symbol.kind for symbol in match_scope.symbols) == (
        SymbolKind.NODE,
        SymbolKind.RELATIONSHIP,
        SymbolKind.NODE,
        SymbolKind.RELATIONSHIP,
        SymbolKind.NODE,
    )


def test_analysis_records_scope_at_each_clause_boundary():
    query = parse_ast(
        "MATCH (a) MATCH (a)-[r:T]->(b) WHERE b.active RETURN b AS person"
    )

    analysis = analyze_query(query)

    assert tuple(scope.names for scope in analysis.clause_scopes) == (
        ("a",),
        ("a", "r", "b"),
        ("a", "r", "b"),
        ("person",),
    )
    assert analysis.clauses[-1].scope_before.names == ("a", "r", "b")
    assert analysis.final_scope.resolve("person").kind is SymbolKind.NODE


def test_analysis_expands_return_wildcard_from_current_named_scope():
    query = parse_ast("MATCH ()-[r:T]->(b), (a) RETURN *")

    analysis = analyze_query(query)

    assert analysis.output_names == ("r", "b", "a")
    assert tuple(item.rendered_expression for item in analysis.clauses[-1].projections) == (
        "r",
        "b",
        "a",
    )


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("MATCH (n) WHERE missing.ok RETURN n", "WHERE references unbound variable: missing"),
        ("MATCH (n) RETURN missing.name", "RETURN references unbound variable: missing"),
        ("MATCH (x)-[x:T]->() RETURN x", "both a node and a relationship"),
        ("MATCH (n) RETURN n.id AS value, n AS value", "RETURN contains duplicate column names"),
    ],
)
def test_parse_ast_uses_semantic_scope_validation(query, message):
    with pytest.raises(CypherSemanticError, match=message):
        parse_ast(query)


def test_legacy_parse_outputs_remain_compatible_with_analyzed_ast():
    query = "MATCH (a)<-[r:T]-(b) RETURN *"

    canonical = parse_ast(query)
    legacy = parse(query)
    analysis = analyze_query(canonical)

    assert analysis.output_names == ("a", "r", "b")
    assert legacy.returns == analysis.output_names
    assert legacy.projections == analysis.output_names


def _projection_graph():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"age": 41, "name": "Ann"}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"age": 30, "name": "Bo"}))
    return graph


def test_general_projection_arithmetic_and_alias_execute():
    result = execute(_projection_graph(), "MATCH (n:Person) RETURN n.age + 1 AS next ORDER BY next")

    assert result.columns == ("next",)
    assert result.records == [{"next": 31}, {"next": 42}]


def test_general_projections_support_literals_params_lists_and_maps():
    graph = _projection_graph()

    assert execute(graph, "MATCH (n:Person) WHERE n.name = 'Ann' RETURN 1 AS one").records == [{"one": 1}]
    assert execute(graph, "MATCH (n:Person) WHERE n.name = 'Ann' RETURN $value AS value", parameters={"value": 7}).records == [
        {"value": 7}
    ]
    assert execute(graph, "MATCH (n:Person) WHERE n.name = 'Ann' RETURN [n.age, 2] AS vals").records == [
        {"vals": [41, 2]}
    ]
    assert execute(graph, "MATCH (n:Person) WHERE n.name = 'Ann' RETURN {age: n.age} AS data").records == [
        {"data": {"age": 41}}
    ]


def test_unaliased_expression_projection_uses_deterministic_name():
    result = execute(_projection_graph(), "MATCH (n:Person) WHERE n.name = 'Ann' RETURN n.age + 1")

    assert result.columns == ("(n.age + 1)",)
    assert result.records == [{"(n.age + 1)": 42}]


def test_order_by_general_expression_without_alias():
    result = execute(
        _projection_graph(),
        "MATCH (n:Person) RETURN n.name AS name ORDER BY n.age + 1 DESC",
    )

    assert [record["name"] for record in result.records] == ["Ann", "Bo"]


def test_mixed_wildcard_projection_is_rejected():
    with pytest.raises(CypherSemanticError, match="must be the only projection item"):
        parse_ast("MATCH (n) RETURN *, n.id")


def test_projection_with_unbound_variable_is_rejected():
    with pytest.raises(CypherSemanticError, match="RETURN references unbound variable"):
        parse_ast("MATCH (n) RETURN missing.id")
