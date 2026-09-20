from contextlib import contextmanager

import pytest

from gestaltdb.cypher import execute, parse
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.query_engine.cypher.ast import MatchClause, OptionalMatchClause
from gestaltdb.query_engine.cypher.errors import CypherSemanticError
from gestaltdb.query_engine.cypher.parser import parse_ast
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInstant
from tests.test_temporal_as_of import _MetadataStore
from tests.test_cypher import FakeCypherGraph


def _memory_graph(tmp_path, name="graph"):
    graph = GraphDB(_MetadataStore(), JSONSerializer())
    graph._backend_name = "leveldb"
    graph.save_manifest(tmp_path / name)
    return graph


@pytest.fixture
def temporal_graph(tmp_path):
    return _memory_graph(tmp_path)


def _at(microseconds: int) -> TemporalInstant:
    return TemporalInstant(microseconds)


def _qualifier(valid="$valid", system=None):
    text = f"FOR VALID_TIME AS OF {valid} "
    if system is not None:
        text += f"FOR SYSTEM_TIME AS OF {system} "
    return text


def test_temporal_qualifier_grammar_ast_and_semantic_errors():
    query = parse_ast(
        "OPTIONAL MATCH (n) "
        "FOR SYSTEM_TIME AS OF $known FOR VALID_TIME AS OF datetime($at) RETURN n"
    )
    clause = query.clauses[0]
    assert isinstance(clause, OptionalMatchClause)
    assert [item.kind for item in clause.qualifiers] == ["system", "valid"]

    with pytest.raises(CypherSemanticError, match="legacy parse"):
        parse("MATCH (n) FOR VALID_TIME AS OF $at RETURN n")
    with pytest.raises(CypherSemanticError, match="duplicate valid-time"):
        parse_ast("MATCH (n) FOR VALID_TIME AS OF $a FOR VALID_TIME AS OF $b RETURN n")
    with pytest.raises(CypherSemanticError, match="cannot reference row variables"):
        parse_ast("MATCH (n) FOR VALID_TIME AS OF n.when RETURN n")
    with pytest.raises(CypherSemanticError, match="requires FOR VALID_TIME"):
        parse_ast("MATCH (n) FOR SYSTEM_TIME AS OF $known RETURN n")
    with pytest.raises(CypherSemanticError, match="read-only"):
        parse_ast("MATCH (n) FOR VALID_TIME AS OF $at SET n.x = 1 RETURN n")


def test_temporal_scan_metadata_and_unqualified_compatibility(temporal_graph):
    version = temporal_graph.put_node_version(
        Node("alice", labels=["Person"], properties={"name": "Historical Alice"}),
        valid=(0, None),
    )

    result = temporal_graph.query(
        "MATCH (n:Person) FOR VALID_TIME AS OF $at "
        "RETURN n.id, versionId(n) AS version, validFrom(n) AS start, "
        "validTo(n) AS finish, systemFrom(n) AS learned",
        parameters={"at": _at(0)},
    )

    assert result.records == [{
        "n.id": "alice",
        "version": version.version_id,
        "start": _at(0),
        "finish": None,
        "learned": version.system_time,
    }]
    current_graph = FakeCypherGraph()
    current_graph.put_node(Node("current", labels=["Person"], properties={"name": "Current"}))
    current = execute(current_graph, "MATCH (n:Person) RETURN n.id, versionId(n) AS version")
    assert current.records == [{"n.id": "current", "version": None}]


def test_temporal_system_horizon_corrections_and_retractions(temporal_graph):
    first = temporal_graph.put_node_version(
        Node("alice", properties={"name": "Alice"}), valid=(0, 100)
    )
    corrected = temporal_graph.correct_node_version(
        Node("alice", properties={"name": "Alicia"}),
        supersedes_version_id=first.version_id,
        valid=(20, 40),
    )
    retracted = temporal_graph.retract_node_version(
        "alice", supersedes_version_id=corrected.version_id, valid=(30, 35)
    )
    query = (
        "MATCH (n {id: 'alice'}) "
        + _qualifier("$valid", "$known")
        + "RETURN n.name, versionId(n) AS version"
    )

    assert temporal_graph.query(
        query, parameters={"valid": _at(25), "known": first.system_time}
    ).records == [{"n.name": "Alice", "version": first.version_id}]
    assert temporal_graph.query(
        query, parameters={"valid": _at(25), "known": corrected.system_time}
    ).records == [{"n.name": "Alicia", "version": corrected.version_id}]
    assert temporal_graph.query(
        query, parameters={"valid": _at(32), "known": retracted.system_time}
    ).records == []
    assert temporal_graph.query(
        query, parameters={"valid": _at(100), "known": retracted.system_time}
    ).records == []


def test_temporal_typed_untyped_optional_variable_and_shortest_paths(temporal_graph):
    for node in (
        Node("a", labels=["Person"]),
        Node("b", labels=["Person"]),
        Node("c", labels=["Person"]),
        Node("orphan", labels=["Person"]),
    ):
        temporal_graph.put_node_version(node, valid=(0, None))
    e1 = temporal_graph.put_edge_version(
        Edge("e1", "a", "b", {"type": "KNOWS"}), valid=(0, None)
    )
    temporal_graph.put_edge_version(Edge("e2", "b", "c", {}), valid=(0, None))

    typed = temporal_graph.query(
        "MATCH (a {id: 'a'})-[r:KNOWS]->(b) "
        + _qualifier()
        + "RETURN b.id, versionId(r) AS version, startNode(r).id AS source",
        parameters={"valid": _at(0)},
    )
    untyped = temporal_graph.query(
        "MATCH (a {id: 'a'})-[rs*1..2]->(b) "
        + _qualifier()
        + "RETURN b.id ORDER BY b.id",
        parameters={"valid": _at(0)},
    )
    shortest = temporal_graph.query(
        "MATCH SHORTEST (a {id: 'a'})-[rs*]->(b {id: 'c'}) "
        + _qualifier()
        + "RETURN length(rs) AS hops",
        parameters={"valid": _at(0)},
    )
    optional = temporal_graph.query(
        "MATCH (a {id: 'orphan'}) "
        "OPTIONAL MATCH (a)-[r]->(b) " + _qualifier() + "RETURN b, versionId(r) AS version",
        parameters={"valid": _at(0)},
    )

    assert typed.records == [{"b.id": "b", "version": e1.version_id, "source": "a"}]
    assert untyped.records == [{"b.id": "b"}, {"b.id": "c"}]
    assert shortest.records == [{"hops": 2}]
    assert optional.records == [{"b": None, "version": None}]


def test_temporal_context_propagates_to_chains_union_and_subqueries(temporal_graph):
    for node in (Node("a"), Node("b"), Node("c")):
        temporal_graph.put_node_version(node, valid=(0, None))
    temporal_graph.put_edge_version(Edge("e1", "a", "b", {"type": "R"}), valid=(0, None))
    temporal_graph.put_edge_version(Edge("e2", "b", "c", {"type": "R"}), valid=(0, None))

    chained = temporal_graph.query(
        "MATCH (a {id: 'a'}) FOR VALID_TIME AS OF $at "
        "MATCH (a)-[:R]->(b) MATCH (b)-[:R]->(c) RETURN c.id",
        parameters={"at": _at(0)},
    )
    unioned = temporal_graph.query(
        "MATCH (n {id: 'a'}) FOR VALID_TIME AS OF $at RETURN n.id AS id "
        "UNION ALL MATCH (n {id: 'c'}) RETURN n.id AS id",
        parameters={"at": _at(0)},
    )
    subquery = temporal_graph.query(
        "MATCH (a {id: 'a'}) FOR VALID_TIME AS OF $at "
        "CALL { MATCH (a)-[:R]->(b) RETURN b } RETURN b.id",
        parameters={"at": _at(0)},
    )

    assert chained.records == [{"c.id": "c"}]
    assert unioned.records == [{"id": "a"}, {"id": "c"}]
    assert subquery.records == [{"b.id": "b"}]


def test_temporal_catalog_rebuild_matches_incremental_and_avoids_history_scan(tmp_path):
    maintained = _memory_graph(tmp_path, "maintained")
    rebuilt = _memory_graph(tmp_path, "rebuilt")
    for graph, mode in ((maintained, "maintain"), (rebuilt, "defer")):
        graph.put_node_version(Node("a"), valid=(0, None), index_mode=mode)
        graph.put_node_version(Node("b"), valid=(0, None), index_mode=mode)
        graph.put_edge_version(Edge("e", "a", "b", {}), valid=(0, None), index_mode=mode)
    query = "MATCH (a)-->(b) FOR VALID_TIME AS OF $at RETURN a.id, b.id"
    with pytest.raises(RuntimeError, match="temporal"):
        rebuilt.query(query, parameters={"at": _at(0)})
    rebuilt.rebuild_temporal_indexes()
    maintained.iter_temporal_commits = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("history scan")
    )

    expected = [{"a.id": "a", "b.id": "b"}]
    assert maintained.query(query, parameters={"at": _at(0)}).records == expected
    assert rebuilt.query(query, parameters={"at": _at(0)}).records == expected


def test_temporal_query_keeps_the_horizon_captured_at_start(temporal_graph):
    first = temporal_graph.put_node_version(
        Node("alice", properties={"name": "Alice"}), valid=(0, None)
    )
    original_read_view = temporal_graph.read_view

    @contextmanager
    def publish_after_capture(**kwargs):
        with original_read_view(**kwargs) as view:
            temporal_graph.correct_node_version(
                Node("alice", properties={"name": "Alicia"}),
                supersedes_version_id=first.version_id,
            )
            yield view

    temporal_graph.read_view = publish_after_capture
    result = temporal_graph.query(
        "MATCH (n {id: 'alice'}) FOR VALID_TIME AS OF $at RETURN n.name",
        parameters={"at": _at(0)},
    )

    assert result.records == [{"n.name": "Alice"}]


def test_temporal_qualifier_runtime_validation_and_conflicts(temporal_graph):
    temporal_graph.put_node_version(Node("n"), valid=(0, None))
    with pytest.raises(TypeError, match="datetime"):
        temporal_graph.query("MATCH (n) FOR VALID_TIME AS OF date('2020-01-01') RETURN n")
    with pytest.raises(ValueError, match="cannot be null"):
        temporal_graph.query("MATCH (n) FOR VALID_TIME AS OF $at RETURN n", {"at": None})
    with pytest.raises(ValueError, match="Conflicting"):
        temporal_graph.query(
            "MATCH (n) FOR VALID_TIME AS OF $a MATCH (m) FOR VALID_TIME AS OF $b RETURN n",
            {"a": _at(0), "b": _at(1)},
        )
