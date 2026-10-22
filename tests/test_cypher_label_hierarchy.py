"""PropEnh-14 (#127) M2: node label hierarchies and opt-in Cypher expansion."""

import pytest

from gestaltdb import LabelHierarchy
from gestaltdb.graphdb import Edge, GraphDB, Node
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
    return LabelHierarchy({
        "Ocean_Freight": ["Transport_Edge"],
        "Air_Freight": ["Transport_Edge"],
        "Refrigerated_Freight": ["Ocean_Freight"],
        "Hazmat": [],
    })


def populate(graph):
    for name, labels in (
        ("generic", ["Transport_Edge"]),
        ("ocean", ["Ocean_Freight"]),
        ("air", ["Air_Freight"]),
        ("refrigerated", ["Refrigerated_Freight"]),
        ("combo", ["Ocean_Freight", "Hazmat"]),
        ("warehouse", ["Warehouse"]),
    ):
        graph.put_node(Node(
            node_id=name, labels=labels, properties={"name": name}
        ))
        if name != "warehouse":
            graph.put_edge(Edge(
                edge_id=f"w-{name}", source="warehouse", target=name,
                properties={"type": "SHIPS_TO"},
            ))


def ids(graph, cypher, label_hierarchy=None, *, read_snapshot=False):
    result = graph.query(
        cypher, label_hierarchy=label_hierarchy, read_snapshot=read_snapshot
    )
    return sorted(record["id"] for record in result.records)


def test_label_hierarchy_validates_and_expands_transitively():
    schema = hierarchy()
    assert schema.closure("Transport_Edge") == (
        "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight",
        "Air_Freight",
    )
    assert schema.closure("Ocean_Freight") == (
        "Ocean_Freight", "Refrigerated_Freight")
    assert schema.expand(["Warehouse", "Ocean_Freight"]) == (
        "Warehouse", "Ocean_Freight", "Refrigerated_Freight")
    assert schema.parents_of("Ocean_Freight") == ("Transport_Edge",)
    assert schema.parents_of("Transport_Edge") == ()
    assert "Ocean_Freight" in schema and "Missing" not in schema
    assert len(schema) == 5
    assert schema == hierarchy()
    assert hash(schema) == hash(hierarchy())
    assert schema != LabelHierarchy({"Ocean_Freight": []})

    with pytest.raises(ValueError, match="label names"):
        LabelHierarchy({"": ["P"]})
    with pytest.raises(ValueError, match="cannot parent itself"):
        LabelHierarchy({"C": ["C"]})
    with pytest.raises(ValueError, match="cycle"):
        LabelHierarchy({"A": ["B"], "B": ["A"]})
    with pytest.raises(ValueError, match="scalar string"):
        LabelHierarchy({"C": "P"})
    with pytest.raises(ValueError, match="sequence, not a scalar"):
        schema.expand("Transport_Edge")
    with pytest.raises(ValueError, match="duplicate child label"):
        LabelHierarchy.from_definitions([("C", ["P"]), ("C", ["Q"])])


def test_default_label_matching_stays_exact(graph):
    populate(graph)
    assert ids(graph, "MATCH (n:Transport_Edge) RETURN n.id AS id") == ["generic"]
    assert ids(graph, "MATCH (n:Ocean_Freight) RETURN n.id AS id") == [
        "combo", "ocean"]


def test_parent_scan_includes_subtypes_and_transitive_descendants(graph):
    populate(graph)
    assert ids(
        graph, "MATCH (n:Transport_Edge) RETURN n.id AS id", hierarchy()
    ) == ["air", "combo", "generic", "ocean", "refrigerated"]
    assert ids(
        graph, "MATCH (n:Ocean_Freight) RETURN n.id AS id", hierarchy()
    ) == ["combo", "ocean", "refrigerated"]
    assert ids(
        graph, "MATCH (n:Missing) RETURN n.id AS id", hierarchy()
    ) == []


def test_label_expressions_apply_hierarchy_to_alternatives_and_negation(graph):
    populate(graph)
    schema = hierarchy()
    assert ids(
        graph, "MATCH (n:Transport_Edge|Warehouse) RETURN n.id AS id", schema
    ) == ["air", "combo", "generic", "ocean", "refrigerated", "warehouse"]
    assert ids(graph, "MATCH (n:!Transport_Edge) RETURN n.id AS id", schema) == [
        "warehouse"]
    assert ids(
        graph, "MATCH (n:Transport_Edge:!Ocean_Freight) RETURN n.id AS id", schema
    ) == ["air", "generic"]


def test_multi_label_intersection_and_endpoint_filters_expand(graph):
    populate(graph)
    schema = hierarchy()
    assert ids(
        graph, "MATCH (n:Transport_Edge:Hazmat) RETURN n.id AS id", schema
    ) == ["combo"]
    assert ids(
        graph,
        "MATCH (a:Warehouse)-[:SHIPS_TO]->(b:Transport_Edge) "
        "RETURN b.id AS id",
        schema,
    ) == ["air", "combo", "generic", "ocean", "refrigerated"]


def test_indexed_lookup_expands_parent_labels(graph):
    populate(graph)
    graph.create_node_property_index("name")
    schema = hierarchy()
    assert ids(
        graph,
        'MATCH (n:Transport_Edge {name: "ocean"}) RETURN n.id AS id',
        schema,
    ) == ["ocean"]
    assert ids(
        graph,
        'MATCH (n:Transport_Edge {name: "warehouse"}) RETURN n.id AS id',
        schema,
    ) == []


def test_returned_labels_remain_literal_and_invalid_hierarchy_is_rejected(graph):
    populate(graph)
    result = graph.query(
        'MATCH (n:Transport_Edge {name: "ocean"}) RETURN labels(n) AS labels',
        label_hierarchy=hierarchy(),
    )
    assert result.records[0]["labels"] == ["Ocean_Freight"]
    with pytest.raises(TypeError, match="LabelHierarchy"):
        graph.query("MATCH (n) RETURN n.id", label_hierarchy={})


def test_authenticated_read_snapshot_path_also_expands_labels(graph):
    populate(graph)
    assert ids(
        graph,
        "MATCH (n:Transport_Edge) RETURN n.id AS id",
        hierarchy(),
        read_snapshot=True,
    ) == ["air", "combo", "generic", "ocean", "refrigerated"]
