"""Terminal Cypher parity and diagnostics coverage ([cypher-21])."""

import pytest

from gestaltdb.cypher import execute, parse, parse_ast, plan
from gestaltdb.query_engine.cypher.ast import ShowIndexes
from gestaltdb.query_engine.cypher.errors import CypherSemanticError, CypherSyntaxError

from tests.conftest import populate_typed_graph
from tests.test_cypher import FakeCypherGraph


def test_show_indexes_lists_explicit_property_indexes_deterministically():
    graph = FakeCypherGraph()
    graph.indexed_node_properties = {"name", "age"}
    graph.indexed_edge_properties = {"score"}

    result = execute(graph, "SHOW INDEXES")

    assert result.columns == ("entityType", "properties")
    assert result.records == [
        {"entityType": "NODE", "properties": ["age"]},
        {"entityType": "NODE", "properties": ["name"]},
        {"entityType": "RELATIONSHIP", "properties": ["score"]},
    ]
    assert execute(graph, "SHOW INDEX").records == result.records
    assert isinstance(parse_ast("SHOW INDEXES"), ShowIndexes)
    with pytest.raises(ValueError, match="legacy parse"):
        parse("SHOW INDEXES")
    with pytest.raises(TypeError, match="Cannot plan"):
        plan("SHOW INDEXES")


def test_generalized_sampling_call_supports_yield_alias_and_parameters(graph_db):
    populate_typed_graph(graph_db)
    result = execute(
        graph_db,
        "CALL pg.sample_typed_paths($seeds, $pattern) YIELD path AS sampled RETURN sampled LIMIT 1",
        parameters={
            "seeds": ["drug-1"],
            "pattern": [
                {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}
            ],
        },
    )

    assert result.columns == ("sampled",)
    assert len(result.records) == 1
    assert result.records[0]["sampled"]["seed"] == b"drug-1"


def test_generalized_procedure_diagnostics_are_semantic():
    with pytest.raises(CypherSemanticError, match="Unsupported procedure: db.labels"):
        parse_ast("CALL db.labels() YIELD label RETURN label")
    with pytest.raises(CypherSemanticError, match="does not yield field: other"):
        parse_ast(
            "CALL pg.sample_typed_paths([], []) YIELD other RETURN other"
        )
    with pytest.raises(CypherSemanticError, match="yielded variable: p"):
        parse_ast(
            "CALL pg.sample_typed_paths([], []) YIELD path AS p RETURN path"
        )


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (a)-[:T]->{1,3}(b) RETURN b",
        "MATCH (a)-[:T]->+(b) RETURN b",
        "MATCH (a)-[:T]->?(b) RETURN b",
        "MATCH ((a)-[:T]->(b)){1,3} RETURN b",
    ],
)
def test_gql_path_quantifiers_raise_migration_hints(query):
    with pytest.raises(CypherSyntaxError, match=r"GQL quantified.*legacy") as error:
        parse_ast(query)
    assert error.value.source == query
    assert error.value.offset > 0


def test_gql_hint_detection_does_not_reject_existing_braces_or_subqueries():
    parse_ast("MATCH (n {bounds: [1, 3]}) RETURN n")
    parse_ast("MATCH (a)-[:T {weight: 1}]->(b) RETURN b")
    parse_ast("CALL { MATCH (n) RETURN n } RETURN n")
