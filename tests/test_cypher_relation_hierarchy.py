"""PropEnh-14 (#127): relation hierarchies and opt-in Cypher expansion."""

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.hierarchy import RelationHierarchy
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        yield db
    finally:
        db.close()


def hierarchy():
    return RelationHierarchy({
        "Ocean_Freight": ["Transport_Edge"],
        "Air_Freight": ["Transport_Edge"],
        "Rail": ["Transport_Edge"],
        "Refrigerated_Freight": ["Ocean_Freight"],
    })


def populate(graph):
    for name in ("hub", "ocean", "reefer", "air", "rail", "generic"):
        graph.put_node(Node(node_id=name, labels=[], properties={}))
    for edge_id, target, edge_type, weight in (
        ("e-ocean", "ocean", "Ocean_Freight", 2),
        ("e-reefer", "reefer", "Refrigerated_Freight", 2),
        ("e-air", "air", "Air_Freight", 3),
        ("e-rail", "rail", "Rail", 4),
        ("e-generic", "generic", "Transport_Edge", 5),
    ):
        graph.put_edge(Edge(
            edge_id=edge_id,
            source="hub",
            target=target,
            properties={"type": edge_type, "weight": weight},
        ))
    graph.create_edge_property_index("weight")


def types(graph, cypher, relation_hierarchy=None, *, read_snapshot=False):
    result = graph.query(
        cypher,
        relation_hierarchy=relation_hierarchy,
        read_snapshot=read_snapshot,
    )
    return sorted(record["t"] for record in result.records)


def test_default_relationship_matching_stays_exact(graph):
    populate(graph)
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Transport_Edge]->(b) RETURN type(r) AS t',
    ) == ["Transport_Edge"]
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Ocean_Freight]->(b) RETURN type(r) AS t',
    ) == ["Ocean_Freight"]


def test_parent_relationship_matches_subproperties_transitively(graph):
    populate(graph)
    schema = hierarchy()
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Transport_Edge]->(b) RETURN type(r) AS t',
        schema,
    ) == ["Air_Freight", "Ocean_Freight", "Rail", "Refrigerated_Freight",
          "Transport_Edge"]
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Ocean_Freight]->(b) RETURN type(r) AS t',
        schema,
    ) == ["Ocean_Freight", "Refrigerated_Freight"]
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Missing]->(b) RETURN type(r) AS t',
        schema,
    ) == []


def test_relationship_property_maps_expand_and_return_literal_types(graph):
    populate(graph)
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Transport_Edge {weight: 2}]->(b) '
        'RETURN type(r) AS t',
        hierarchy(),
    ) == ["Ocean_Freight", "Refrigerated_Freight"]


def test_indexed_unbound_relationship_lookup_expands_types(graph):
    populate(graph)
    assert types(
        graph,
        'MATCH ()-[r:Transport_Edge {weight: 2}]->(b) RETURN type(r) AS t',
        hierarchy(),
    ) == ["Ocean_Freight", "Refrigerated_Freight"]
    assert types(
        graph,
        'MATCH ()-[r:Transport_Edge]->(b) WHERE r.weight >= 3 '
        'RETURN type(r) AS t',
        hierarchy(),
    ) == ["Air_Freight", "Rail", "Transport_Edge"]


def test_variable_length_paths_expand_relationship_types(graph):
    populate(graph)
    result = graph.query(
        'MATCH p=(a {id: "hub"})-[:Transport_Edge*1..1]->(b) '
        'RETURN count(p) AS n',
        relation_hierarchy=hierarchy(),
    )
    assert result.records[0]["n"] == 5
    literal = graph.query(
        'MATCH p=(a {id: "hub"})-[:Transport_Edge*1..1]->(b) '
        'RETURN count(p) AS n',
    )
    assert literal.records[0]["n"] == 1


def test_unanchored_and_authenticated_snapshot_paths_expand(graph):
    populate(graph)
    schema = hierarchy()
    assert types(
        graph,
        'MATCH ()-[r:Transport_Edge]->(b) RETURN type(r) AS t',
        schema,
    ) == ["Air_Freight", "Ocean_Freight", "Rail", "Refrigerated_Freight",
          "Transport_Edge"]
    assert types(
        graph,
        'MATCH (a {id: "hub"})-[r:Transport_Edge]->(b) RETURN type(r) AS t',
        schema,
        read_snapshot=True,
    ) == ["Air_Freight", "Ocean_Freight", "Rail", "Refrigerated_Freight",
          "Transport_Edge"]


def test_invalid_relation_hierarchy_is_rejected(graph):
    populate(graph)
    with pytest.raises(TypeError, match="RelationHierarchy"):
        graph.query(
            'MATCH (a {id: "hub"})-[r:Transport_Edge]->(b) RETURN r',
            relation_hierarchy={},
        )
