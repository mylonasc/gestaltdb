import pytest

from gestaltdb.cypher import parse_ast
from gestaltdb.cypher_parser import CypherSemanticError, parse
from gestaltdb.cypher_semantics import SymbolKind, analyze_query


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
