"""Planner, index-selection, and LIMIT/write-barrier coverage ([cypher-19])."""

from gestaltdb.cypher import execute
from gestaltdb.graphdb import Edge, Node

from tests.test_cypher import FakeCypherGraph


class RecordingIndexGraph(FakeCypherGraph):
    def __init__(self):
        super().__init__()
        self.indexed_edge_properties = set()
        self.calls = []

    def count_nodes_by_label(self, label):
        return len(self.labels.get(label, ()))

    def iter_node_ids_by_label(self, label):
        self.calls.append(("label", label))
        yield from self.labels.get(label, ())

    def iter_node_ids_by_label_property(self, label, property_name, value):
        self.calls.append(("node_exact", label, property_name, value))
        for node_id in self.labels.get(label, ()):
            if self.nodes[node_id].properties.get(property_name) == value:
                yield node_id

    def iter_node_ids_by_label_property_range(
        self,
        label,
        property_name,
        start_value,
        end_value,
        include_start=True,
        include_end=True,
    ):
        self.calls.append(
            ("node_range", label, property_name, start_value, end_value)
        )
        for node_id in self.labels.get(label, ()):
            value = self.nodes[node_id].properties.get(property_name)
            if value is None:
                continue
            if start_value is not None and (
                value < start_value or (value == start_value and not include_start)
            ):
                continue
            if end_value is not None and (
                value > end_value or (value == end_value and not include_end)
            ):
                continue
            yield node_id

    def iter_edge_ids_by_type_property(self, edge_type, property_name, value):
        self.calls.append(("edge_exact", edge_type, property_name, value))
        for edge_id, edge in self.edges.items():
            if edge.get_type == edge_type and edge.properties.get(property_name) == value:
                yield edge_id

    def iter_typed_adjacency(self, node_id, edge_type, direction="out"):
        self.calls.append(("typed_adjacency", node_id, edge_type))
        yield from super().iter_typed_adjacency(node_id, edge_type, direction)

    def iter_edge_ids(self, num_edges=None, key_offset=None):
        self.calls.append(("all_edges",))
        yield from super().iter_edge_ids(num_edges, key_offset)


def _recording_graph():
    graph = RecordingIndexGraph()
    graph.put_node(
        Node(
            node_id="a",
            labels=["Person", "Large"],
            properties={"status": "active", "email": "a@example.test", "age": 30},
        )
    )
    graph.put_node(
        Node(
            node_id="b",
            labels=["Person", "Large", "Small"],
            properties={"status": "inactive", "email": "b@example.test", "age": 40},
        )
    )
    graph.put_edge(
        Edge(edge_id="e", source="a", target="b", properties={"type": "KNOWS", "score": 2})
    )
    return graph


def test_match_orders_independent_node_patterns_by_label_cardinality():
    graph = _recording_graph()

    records = execute(
        graph,
        "MATCH (large:Large), (small:Small) RETURN large.id, small.id LIMIT 1",
    ).records

    assert records == [{"large.id": "a", "small.id": "b"}]
    assert next(call for call in graph.calls if call[0] == "label") == ("label", "Small")


def test_inline_node_map_uses_any_eligible_indexed_property():
    graph = _recording_graph()
    graph.indexed_node_properties = {"email"}

    records = execute(
        graph,
        "MATCH (n:Person {status: 'active', email: 'a@example.test'}) RETURN n.id",
    ).records

    assert records == [{"n.id": "a"}]
    assert ("node_exact", "Person", "email", "a@example.test") in graph.calls


def test_where_range_seek_is_not_suppressed_by_inline_properties():
    graph = _recording_graph()
    graph.indexed_node_properties = {"age"}

    records = execute(
        graph,
        "MATCH (n:Person {status: 'active'}) WHERE n.age >= 25 RETURN n.id",
    ).records

    assert records == [{"n.id": "a"}]
    assert ("node_range", "Person", "age", 25, None) in graph.calls


def test_inline_relationship_map_uses_typed_property_index():
    graph = _recording_graph()
    graph.indexed_edge_properties = {"score"}

    records = execute(
        graph,
        "MATCH (a)-[:KNOWS {score: 2}]->(b) RETURN b.id",
    ).records

    assert records == [{"b.id": "b"}]
    assert ("edge_exact", "KNOWS", "score", 2) in graph.calls


def test_typed_expansion_avoids_untyped_all_edge_scan():
    graph = _recording_graph()

    assert execute(
        graph, "MATCH (a {id: 'a'})-[:KNOWS]->(b) RETURN b.id"
    ).records == [{"b.id": "b"}]
    assert ("typed_adjacency", b"a", "KNOWS") in graph.calls
    assert ("all_edges",) not in graph.calls

    graph.calls.clear()
    assert execute(
        graph, "MATCH (a {id: 'a'})-->(b) RETURN b.id"
    ).records == [{"b.id": "b"}]
    assert ("all_edges",) in graph.calls


def test_terminal_limit_does_not_truncate_prior_writes():
    graph = _recording_graph()

    records = execute(
        graph,
        "MATCH (n:Person) SET n.seen = true RETURN n.id LIMIT 1",
    ).records

    assert len(records) == 1
    assert all(node.properties.get("seen") is True for node in graph.nodes.values())


def test_terminal_limit_zero_still_executes_prior_writes():
    graph = _recording_graph()

    records = execute(
        graph,
        "MATCH (n:Person) SET n.seen = true RETURN n.id LIMIT 0",
    ).records

    assert records == []
    assert all(node.properties.get("seen") is True for node in graph.nodes.values())


def test_with_limit_before_write_restricts_write_input():
    graph = _recording_graph()

    execute(graph, "MATCH (n:Person) WITH n LIMIT 1 SET n.seen = true RETURN n.id")

    assert sum(node.properties.get("seen") is True for node in graph.nodes.values()) == 1


def test_cypher_bulk_writes_maintain_range_indexes(lmdb_graph_db):
    graph = lmdb_graph_db
    graph.create_node_property_index("age")
    graph.create_edge_property_index("score")

    execute(
        graph,
        "CREATE (a:Person {id: 'a', age: 20})-[r:KNOWS {id: 'e', score: 5}]->"
        "(b:Person {id: 'b', age: 40}) RETURN r.id",
    )

    assert list(graph.iter_node_ids_by_label_property_range("Person", "age", 30, None)) == [b"b"]
    assert list(graph.iter_edge_ids_by_type_property_range("KNOWS", "score", 4, 6)) == [b"e"]
