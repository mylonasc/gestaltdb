from gestaltdb.graphdb import Edge, Node

from .conftest import populate_typed_graph


def test_typed_adjacency_filters_type_and_direction(graph_db):
    populate_typed_graph(graph_db)

    assert set(graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out")) == {b"protein-1", b"protein-2"}
    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="in") == []
    assert graph_db.neighbors_by_edge_type("protein-1", "drug-to-protein", direction="in") == [b"drug-1"]
    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-disease", direction="out") == [b"disease-1"]


def test_deleting_typed_edge_removes_typed_adjacency(graph_db):
    populate_typed_graph(graph_db)

    graph_db.delete_edge(b"d1-p1")

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-2"]
    assert graph_db.neighbors_by_edge_type("protein-1", "drug-to-protein", direction="in") == []


def test_replacing_typed_edge_removes_stale_typed_adjacency(graph_db):
    populate_typed_graph(graph_db)

    graph_db.put_edge(Edge(edge_id="d1-p1", source="drug-1", target="disease-2", properties={"type": "drug-to-disease"}))

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-2"]
    assert set(graph_db.neighbors_by_edge_type("drug-1", "drug-to-disease", direction="out")) == {b"disease-1", b"disease-2"}


def test_rebuild_typed_adjacency_from_edge_records(graph_db):
    for node_id in ["drug-1", "protein-1"]:
        graph_db.put_node(Node(node_id=node_id))
    edge = Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"})
    graph_db.store.put_edge(edge.get_id_bytes, graph_db.entity_serializer.serialize(edge, "Edge"))

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == []

    assert graph_db.rebuild_typed_adjacency() == 1
    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]


def test_relationship_type_index_tracks_bulk_edges(graph_db):
    populate_typed_graph(graph_db)

    assert {edge.get_id for edge in graph_db.edges_by_type("drug-to-protein")} == {"d1-p1", "d1-p2", "d2-p3"}


def test_exact_edge_property_index_supports_lookup(graph_db):
    for node_id in ["drug-1", "protein-1", "protein-2"]:
        graph_db.put_node(Node(node_id=node_id))
    graph_db.put_edges_bulk([
        Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein", "score": 1}),
        Edge(edge_id="d1-p2", source="drug-1", target="protein-2", properties={"type": "drug-to-protein", "score": 2}),
    ])

    assert graph_db.create_edge_property_index("score") == 2
    assert [edge.get_id for edge in graph_db.edges_by_property("score", 2)] == ["d1-p2"]
