"""Real-backend Cypher corpus replay and FakeCypherGraph contract checks.

The broad Cypher suite runs mostly against ``FakeCypherGraph``. These tests
replay the same read/write semantics and mutation contracts against real
LMDB/LevelDB/PyRex storage so the fake cannot drift into accepting behavior the
backends reject (or vice versa). Results are compared after ordering explicitly;
un-ordered scans are only compared through normalized dumps.
"""

import json

import pytest

from gestaltdb.cypher import execute
from gestaltdb.graphdb import Edge, Node
from gestaltdb.query_engine.cypher.ast import PathValue
from tests.test_cypher import FakeCypherGraph


def _seed(graph):
    graph.put_node(Node("alice", labels=["Person", "Employee"],
                        properties={"name": "Alice", "age": 30, "city": "Athens"}))
    graph.put_node(Node("bob", labels=["Person"],
                        properties={"name": "Bob", "age": 40, "city": "Berlin"}))
    graph.put_node(Node("acme", labels=["Org", "Company"],
                        properties={"name": "Acme", "age": 100}))
    graph.put_node(Node("carol", labels=["Person"],
                        properties={"name": "Carol", "age": 25, "city": "Athens"}))
    graph.put_edge(Edge("e-ab", "alice", "bob", {"type": "KNOWS", "since": 2015}))
    graph.put_edge(Edge("e-bc", "bob", "carol", {"type": "KNOWS", "since": 2018}))
    graph.put_edge(Edge("e-ac", "alice", "acme", {"type": "WORKS_AT", "role": "engineer"}))


def _freeze(value):
    return json.dumps(value, sort_keys=True, default=repr)


def _normalize(value):
    """Map graph values to stable, comparable structures across backends."""
    if isinstance(value, Node):
        return ("node", value.get_id, tuple(sorted(value.labels)), _freeze(value.properties))
    if isinstance(value, Edge):
        return ("edge", value.get_id, value.source, value.target, _freeze(value.properties))
    if isinstance(value, PathValue):
        return ("path", tuple(_normalize(node) for node in value.nodes),
                tuple(_normalize(edge) for edge in value.edges))
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _records(result):
    return [_normalize(record) for record in result.records]


def _dump(graph):
    nodes = []
    for node_id in sorted(graph.get_node_keys_generator()):
        node = graph.get_node(node_id)
        nodes.append([node.get_id, sorted(node.labels), _freeze(node.properties)])
    edges = []
    for edge_id in sorted(graph.iter_edge_ids()):
        edge = graph.get_edge(edge_id)
        # CREATE/MERGE generate random edge IDs; compare endpoint/type/properties.
        edges.append([edge.source, edge.target, _freeze(edge.properties)])
    return {"nodes": nodes, "edges": sorted(edges)}


READ_CASES = [
    pytest.param(
        'MATCH (p:Person) WHERE p.age >= 30 RETURN p.id AS id, p.name AS name ORDER BY id',
        id="label-property-filter"),
    pytest.param(
        'MATCH (n:Person:Employee) RETURN n.id AS id ORDER BY id',
        id="multi-label"),
    pytest.param(
        'MATCH (n:Person|Org) RETURN n.id AS id ORDER BY id',
        id="label-alternatives"),
    pytest.param(
        'MATCH (n:!Person) RETURN n.id AS id ORDER BY id',
        id="label-negation"),
    pytest.param(
        'MATCH (a:Person)-[r:KNOWS {since: 2015}]->(b) RETURN a.id AS a, b.id AS b, r.since AS since',
        id="typed-relationship-property-map"),
    pytest.param(
        'MATCH p=(a:Person {id: "alice"})-[:KNOWS*1..2]->(b) '
        'RETURN length(p) AS hops, b.id AS id ORDER BY hops, id',
        id="variable-length-path"),
    pytest.param(
        'MATCH SHORTEST (a:Person {id: "alice"})-[:KNOWS*]->(b:Person {id: "carol"}) '
        'RETURN b.id AS id',
        id="shortest-path"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) OPTIONAL MATCH (a)-[:MISSING]->(x) '
        'RETURN a.id AS a, x.id AS x',
        id="optional-match"),
    pytest.param(
        'MATCH (p:Person) WITH p.city AS city, count(*) AS people, avg(p.age) AS mean_age '
        'RETURN city, people, mean_age ORDER BY city',
        id="with-aggregation"),
    pytest.param(
        'MATCH (p:Person) RETURN collect(DISTINCT p.city) AS cities, '
        'count(DISTINCT p.city) AS city_count, min(p.age) AS youngest, max(p.age) AS oldest',
        id="aggregate-distinct"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) WITH a UNWIND [1, 2, 3] AS n '
        'RETURN a.id AS a, n ORDER BY n',
        id="unwind-pipeline"),
    pytest.param(
        'MATCH (p:Person) RETURN p.city AS city ORDER BY city SKIP 1 LIMIT 1',
        id="skip-limit"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) RETURN a.id AS id '
        'UNION MATCH (b:Person {id: "bob"}) RETURN b.id AS id',
        id="union-dedup"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) RETURN a.id AS id '
        'UNION ALL MATCH (b:Person {id: "bob"}) RETURN b.id AS id',
        id="union-all"),
    pytest.param(
        'MATCH (p:Person) WHERE p.name STARTS WITH "A" OR p.name =~ ".*o.*" '
        'RETURN p.id AS id ORDER BY id',
        id="string-predicates"),
    pytest.param(
        'MATCH (p:Person) WHERE p.city IN ["Athens", "Berlin"] AND p.missing IS NULL '
        'RETURN p.id AS id ORDER BY id',
        id="in-null-predicates"),
    pytest.param(
        'MATCH (p:Person) WHERE exists(p.city) RETURN count(*) AS n',
        id="exists-property"),
    pytest.param(
        'MATCH (p:Person) RETURN * ORDER BY p.id',
        id="return-star"),
    pytest.param(
        'MATCH (p:Person {id: "alice"}) RETURN '
        'CASE WHEN p.age > 18 THEN "adult" ELSE "minor" END AS band, '
        '[x IN [1, 2, 3] WHERE x > 1 | x * 2] AS doubled, '
        'reduce(total = 0, x IN [1, 2, 3] | total + x) AS total',
        id="projection-expressions"),
    pytest.param(
        'MATCH (a:Person {id: "alice"})-[r:KNOWS]->(b) '
        'RETURN a.id AS a, b.id AS b, type(r) AS kind, startNode(r).id AS source, '
        'endNode(r).id AS target, properties(r) AS props ORDER BY b',
        id="relationship-accessors"),
    pytest.param(
        'MATCH (p:Person) WHERE NOT p.age < 30 RETURN p.id AS id ORDER BY id',
        id="not-predicate"),
    pytest.param(
        'MATCH (n:Person) RETURN count(n) AS people, count(*) AS rows',
        id="count-forms"),
]


WRITE_CASES = [
    pytest.param(
        'CREATE (n:Person {id: "dave", name: "Dave", age: 22}) RETURN n.id AS id',
        id="create-node"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}), (b:Person {id: "bob"}) '
        'CREATE (a)-[r:MENTORS {since: 2024}]->(b) RETURN type(r) AS kind',
        id="create-edge-between-bound-nodes"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) SET a.age = 31, a:VIP RETURN a.age AS age, labels(a) AS labels',
        id="set-property-and-label"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) SET a += {city: "Paris", active: true} '
        'RETURN a.city AS city, a.active AS active',
        id="set-merge-map"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) REMOVE a.city, a:Employee RETURN a.city AS city, labels(a) AS labels',
        id="remove-property-and-label"),
    pytest.param(
        'MATCH (:Person {id: "alice"})-[r:KNOWS]->() DELETE r RETURN count(*) AS deleted',
        id="delete-edge"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) DETACH DELETE a RETURN count(*) AS deleted',
        id="detach-delete-node"),
    pytest.param(
        'MERGE (n:Person {id: "dave"}) ON CREATE SET n.name = "Dave" '
        'RETURN n.id AS id, n.name AS name',
        id="merge-create"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) MERGE (a)-[r:MENTORS]->(:Person {id: "bob"}) '
        'ON MATCH SET r.since = 2024 RETURN type(r) AS kind',
        id="merge-match"),
    pytest.param(
        'MATCH (a:Person {id: "alice"}) '
        'FOREACH (x IN [1, 2] | CREATE (:Tag {id: "tag-" + toString(x)})) '
        'RETURN a.id AS id',
        id="foreach-create"),
    pytest.param(
        'UNWIND ["x", "y"] AS suffix CREATE (:Code {id: suffix}) RETURN count(*) AS created',
        id="unwind-create"),
]


@pytest.fixture
def fake_graph():
    graph = FakeCypherGraph()
    _seed(graph)
    return graph


@pytest.fixture
def real_graph(graph_db):
    _seed(graph_db)
    return graph_db


@pytest.mark.parametrize("query", READ_CASES)
def test_read_corpus_matches_fake(real_graph, fake_graph, query):
    fake_result = execute(fake_graph, query)
    real_result = execute(real_graph, query)

    assert real_result.columns == fake_result.columns
    assert _records(real_result) == _records(fake_result)


@pytest.mark.parametrize("query", WRITE_CASES)
def test_write_corpus_matches_fake(real_graph, fake_graph, query):
    fake_result = execute(fake_graph, query)
    real_result = execute(real_graph, query)

    assert real_result.columns == fake_result.columns
    assert _records(real_result) == _records(fake_result)
    assert _dump(real_graph) == _dump(fake_graph)


def test_fake_relabel_removes_old_label_membership(real_graph, fake_graph):
    for graph in (real_graph, fake_graph):
        graph.put_node(Node("alice", labels=["VIP"], properties={"name": "Alice"}))

    assert sorted(real_graph.iter_node_ids_by_label("Person")) == sorted(
        fake_graph.iter_node_ids_by_label("Person"))
    assert sorted(real_graph.iter_node_ids_by_label("VIP")) == sorted(
        fake_graph.iter_node_ids_by_label("VIP"))
    assert _dump(real_graph) == _dump(fake_graph)


def test_fake_edge_replacement_removes_old_type_and_endpoint(real_graph, fake_graph):
    for graph in (real_graph, fake_graph):
        graph.put_edge(Edge("e-ab", "bob", "carol", {"type": "REPORTS_TO", "since": 2020}))

    assert sorted(real_graph.iter_edge_ids_by_type("KNOWS")) == sorted(
        fake_graph.iter_edge_ids_by_type("KNOWS"))
    assert sorted(real_graph.iter_edge_ids_by_type("REPORTS_TO")) == sorted(
        fake_graph.iter_edge_ids_by_type("REPORTS_TO"))
    assert _dump(real_graph) == _dump(fake_graph)


def test_fake_repeated_put_does_not_duplicate_membership(real_graph, fake_graph):
    for graph in (real_graph, fake_graph):
        graph.put_node(Node("alice", labels=["Person", "Employee"],
                            properties={"name": "Alice", "age": 30, "city": "Athens"}))
        graph.put_edge(Edge("e-ab", "alice", "bob", {"type": "KNOWS", "since": 2015}))

    for label in ("Person", "Employee"):
        assert sorted(real_graph.iter_node_ids_by_label(label)) == sorted(
            fake_graph.iter_node_ids_by_label(label))
    real_neighbors = [row["neighbor_id"] for row in real_graph.iter_typed_adjacency(b"alice", "KNOWS")]
    fake_neighbors = [row["neighbor_id"] for row in fake_graph.iter_typed_adjacency(b"alice", "KNOWS")]
    assert sorted(real_neighbors) == sorted(fake_neighbors) == [b"bob"]


def test_fake_delete_node_cascades_like_real_storage(real_graph, fake_graph):
    for graph in (real_graph, fake_graph):
        graph.delete_node(b"bob")

    assert real_graph.get_node(b"bob") is None
    assert fake_graph.get_node(b"bob") is None
    assert _dump(real_graph) == _dump(fake_graph)
    assert sorted(real_graph.iter_edge_ids_by_type("KNOWS")) == sorted(
        fake_graph.iter_edge_ids_by_type("KNOWS")) == []
