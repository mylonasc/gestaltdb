"""Constraint catalog and Cypher write enforcement coverage ([cypher-19])."""

import importlib.util

import pytest

from gestaltdb.cypher import execute, parse_ast
from gestaltdb.query_engine.cypher.ast import CreateConstraint, DropConstraint, ShowConstraints
from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import PickleSerializer

from tests.test_cypher import FakeCypherGraph


def test_constraint_commands_parse_and_execute():
    graph = FakeCypherGraph()

    assert parse_ast(
        "CREATE CONSTRAINT person_email FOR (n:Person) REQUIRE n.email IS UNIQUE"
    ) == CreateConstraint("Person", "email", "unique", "person_email")
    assert isinstance(parse_ast("SHOW CONSTRAINTS"), ShowConstraints)
    assert parse_ast("DROP CONSTRAINT person_email") == DropConstraint("person_email")

    execute(
        graph,
        "CREATE CONSTRAINT person_email FOR (n:Person) REQUIRE n.email IS UNIQUE",
    )
    assert execute(graph, "SHOW CONSTRAINTS").records == [
        {
            "name": "person_email",
            "type": "UNIQUE",
            "label": "Person",
            "property": "email",
        }
    ]
    execute(graph, "DROP CONSTRAINT person_email")
    assert execute(graph, "SHOW CONSTRAINTS").records == []


def test_constraint_creation_validates_existing_nodes():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a", labels=["Person"], properties={"email": "same"}))
    graph.put_node(Node(node_id="b", labels=["Person"], properties={"email": "same"}))

    with pytest.raises(ValueError, match="must be unique"):
        execute(graph, "CREATE CONSTRAINT FOR (n:Person) REQUIRE n.email IS UNIQUE")
    with pytest.raises(ValueError, match="requires non-null"):
        execute(graph, "CREATE CONSTRAINT FOR (n:Person) REQUIRE n.name IS NOT NULL")


def test_constraints_enforce_final_merge_and_update_state():
    graph = FakeCypherGraph()
    execute(graph, "CREATE CONSTRAINT FOR (n:Person) REQUIRE n.email IS UNIQUE")
    execute(graph, "CREATE CONSTRAINT FOR (n:Person) REQUIRE n.email IS NOT NULL")

    records = execute(
        graph,
        "MERGE (n:Person {id: 'a'}) ON CREATE SET n.email = 'a@example.test' RETURN n.id",
    ).records
    assert records == [{"n.id": "a"}]

    with pytest.raises(ValueError, match="must be unique"):
        execute(
            graph,
            "MERGE (n:Person {id: 'b'}) ON CREATE SET n.email = 'a@example.test' RETURN n.id",
        )
    with pytest.raises(ValueError, match="requires non-null"):
        execute(graph, "MATCH (n {id: 'a'}) REMOVE n.email RETURN n.id")

    assert graph.get_node(b"b") is None
    assert graph.get_node(b"a").properties["email"] == "a@example.test"


@pytest.mark.skipif(importlib.util.find_spec("lmdb") is None, reason="lmdb not installed")
def test_constraint_catalog_persists_across_reopen(tmp_path):
    path = str(tmp_path / "constraints")
    graph = GraphDB(LMDBStore(path=path), PickleSerializer())
    execute(
        graph,
        "CREATE CONSTRAINT person_email FOR (n:Person) REQUIRE n.email IS UNIQUE",
    )
    graph.close()

    reopened = GraphDB(LMDBStore(path=path), PickleSerializer())
    try:
        assert execute(reopened, "SHOW CONSTRAINTS").records == [
            {
                "name": "person_email",
                "type": "UNIQUE",
                "label": "Person",
                "property": "email",
            }
        ]
        execute(reopened, "CREATE (n:Person {id: 'a', email: 'same'}) RETURN n.id")
        with pytest.raises(ValueError, match="must be unique"):
            execute(reopened, "CREATE (n:Person {id: 'b', email: 'same'}) RETURN n.id")
    finally:
        reopened.close()
