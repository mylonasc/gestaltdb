import pytest

from gestaltdb.graphdb import Edge, Node
from gestaltdb.cypher import QueryResult, _split_top_level_args, execute, parse, plan
from gestaltdb.cypher_ast import MultiMatchQuery, Parameter
from gestaltdb.cypher_plan import Expand, Limit, NodeAllScan, NodeByIdSeek, NodeLabelScan, NodePropertySeek, ProcedureCall, Project, RelationshipPropertyRangeSeek, RelationshipPropertySeek, RelationshipTypeScan
from gestaltdb.cypher_parser import parse_literal
from gestaltdb.cypher_runtime import QueryContext, execute_match, execute_node_scan, expand_typed

from .conftest import populate_typed_graph


class FakeCypherGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.adjacency = {}
        self.labels = {}
        self.indexed_node_properties = set()
        self.label_yields = []
        self.node_key_yields = []

    def put_node(self, node):
        node_id = node.get_id_bytes
        self.nodes[node_id] = node
        for label in node.labels:
            self.labels.setdefault(label, []).append(node_id)

    def put_edge(self, edge):
        edge_id = edge.get_id_bytes
        self.edges[edge_id] = edge
        edge_type = edge.get_type
        self.adjacency.setdefault((edge.source.encode("utf-8"), edge_type, "out"), []).append((edge_id, edge.target.encode("utf-8")))
        self.adjacency.setdefault((edge.target.encode("utf-8"), edge_type, "in"), []).append((edge_id, edge.source.encode("utf-8")))

    def node_key_to_bytes(self, node_key):
        if isinstance(node_key, bytes):
            return node_key
        return node_key.encode("utf-8")

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def get_edge(self, edge_id):
        return self.edges.get(edge_id)

    def iter_node_ids_by_label(self, label):
        for node_id in self.labels.get(label, []):
            self.label_yields.append(node_id)
            yield node_id

    def iter_node_ids_by_property(self, property_name, value):
        for node_id, node in self.nodes.items():
            if node.properties.get(property_name) == value:
                yield node_id

    def iter_edge_ids_by_type(self, edge_type):
        for edge_id, edge in self.edges.items():
            if edge.get_type == edge_type:
                yield edge_id

    def get_node_keys_generator(self):
        for node_id in self.nodes:
            self.node_key_yields.append(node_id)
            yield node_id

    def iter_typed_adjacency(self, node_id, edge_type, direction="out"):
        directions = ["out", "in"] if direction == "any" else [direction]
        for current_direction in directions:
            for edge_id, neighbor_id in self.adjacency.get((node_id, edge_type, current_direction), []):
                edge = self.edges[edge_id]
                source_id = edge.source.encode("utf-8")
                target_id = edge.target.encode("utf-8")
                yield {
                    "edge_id": edge_id,
                    "neighbor_id": neighbor_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "edge_type": edge_type,
                    "direction": current_direction,
                }


def test_cypher_label_scan_uses_indexed_labels(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}),
        Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}),
    ])

    result = graph_db.query("MATCH (n:Drug) RETURN n")

    assert result.columns == ("n",)
    assert [record["n"].get_id for record in result] == ["drug-1"]


def test_cypher_label_scan_filters_property_with_index_when_registered(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug", "name": "Aspirin"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"kind": "drug", "name": "Ibuprofen"}),
    ])
    graph_db.create_node_property_index("name")

    result = graph_db.query('MATCH (n:Drug {name: "Aspirin"}) RETURN n')

    assert [record["n"].get_id for record in result] == ["drug-1"]


def test_cypher_label_property_filter_uses_composite_index_when_registered(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}),
    ])
    graph_db.create_node_property_index("name")
    calls = []
    original = graph_db.iter_node_ids_by_label_property

    def iter_node_ids_by_label_property(label, property_name, value):
        calls.append((label, property_name, value))
        yield from original(label, property_name, value)

    graph_db.iter_node_ids_by_label_property = iter_node_ids_by_label_property

    result = graph_db.query('MATCH (n:Drug {name: "Aspirin"}) RETURN n.id')

    assert result.records == [{"n.id": "drug-1"}]
    assert calls == [("Drug", "name", "Aspirin")]


def test_cypher_label_scan_filters_property_without_registered_index(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}),
        Node(node_id="drug-3", labels=["Drug"], properties={}),
    ])

    result = graph_db.query('MATCH (n:Drug {name: "Aspirin"}) RETURN n')

    assert [record["n"].get_id for record in result] == ["drug-1"]


def test_cypher_label_scan_filters_parameterized_property_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}))
    graph.put_node(Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}))

    result = execute(graph, "MATCH (n:Drug {name: $name}) RETURN n.id", parameters={"name": "Aspirin"})

    assert result.records == [{"n.id": "drug-1"}]


def test_cypher_node_scan_supports_multiple_labels_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug", "Approved"]))
    graph.put_node(Node(node_id="drug-2", labels=["Drug"]))
    graph.put_node(Node(node_id="compound-1", labels=["Compound", "Approved"]))

    result = execute(graph, "MATCH (n:Drug:Approved) RETURN n.id")

    parsed = parse("MATCH (n:Drug:Approved) RETURN n.id")

    assert parsed.label == "Drug"
    assert parsed.labels == ("Drug", "Approved")
    assert result.records == [{"n.id": "drug-1"}]


def test_cypher_multi_label_scan_can_filter_property_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug", "Approved"], properties={"name": "Aspirin"}))
    graph.put_node(Node(node_id="drug-2", labels=["Drug", "Approved"], properties={"name": "Ibuprofen"}))

    result = execute(graph, 'MATCH (n:Drug:Approved {name: "Aspirin"}) RETURN n.id')

    assert result.records == [{"n.id": "drug-1"}]


def test_cypher_parameterized_property_requires_parameter_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}))

    with pytest.raises(ValueError, match="Missing Cypher parameter"):
        execute(graph, "MATCH (n:Drug {name: $name}) RETURN n.id")


def test_cypher_parser_supports_cypher_literals_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Flag"], properties={"active": True, "missing": None}))
    graph.put_node(Node(node_id="n2", labels=["Flag"], properties={"active": False, "missing": "value"}))

    assert execute(graph, "MATCH (n:Flag {active: true}) RETURN n.id").records == [{"n.id": "n1"}]
    assert execute(graph, "MATCH (n:Flag {active: false}) RETURN n.id").records == [{"n.id": "n2"}]
    assert execute(graph, "MATCH (n:Flag {missing: null}) RETURN n.id").records == [{"n.id": "n1"}]


def test_cypher_node_scan_where_equality_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}))
    graph.put_node(Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}))

    result = execute(graph, 'MATCH (n:Drug) WHERE n.name = "Aspirin" RETURN n.id')

    assert result.records == [{"n.id": "drug-1"}]


def test_cypher_node_scan_where_comparison_and_parameter_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 30}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40}))

    result = execute(graph, "MATCH (n:Person) WHERE n.age >= $age RETURN n.id", parameters={"age": 35})

    assert result.records == [{"n.id": "n2"}]


def test_cypher_node_scan_where_comparison_uses_range_index(graph_db):
    graph_db.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 30}))
    graph_db.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40}))
    graph_db.put_node(Node(node_id="n3", labels=["Person"], properties={"age": 50}))
    graph_db.create_node_property_index("age")
    original = graph_db.iter_node_ids_by_label_property_range
    calls = []

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    graph_db.iter_node_ids_by_label_property_range = tracked

    result = graph_db.query("MATCH (n:Person) WHERE n.age >= $age RETURN n.id", parameters={"age": 35})

    assert calls == [(('Person', 'age', 35, None, True, True), {})]
    assert result.records == [{"n.id": "n2"}, {"n.id": "n3"}]


def test_cypher_node_scan_where_inequality_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"status": "active"}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"status": "inactive"}))

    result = execute(graph, 'MATCH (n:Person) WHERE n.status <> "inactive" RETURN n.id')

    assert result.records == [{"n.id": "n1"}]


def test_cypher_node_scan_where_in_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"status": "active"}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"status": "inactive"}))

    result = execute(graph, 'MATCH (n:Person) WHERE n.status IN ["active", "pending"] RETURN n.id')

    assert result.records == [{"n.id": "n1"}]


def test_cypher_node_scan_where_parameterized_in_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"status": "active"}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"status": "inactive"}))

    result = execute(graph, "MATCH (n:Person) WHERE n.status IN $statuses RETURN n.id", parameters={"statuses": ["inactive"]})

    assert result.records == [{"n.id": "n2"}]


def test_cypher_node_scan_where_null_predicates_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"name": "Alice"}))

    assert execute(graph, "MATCH (n:Person) WHERE n.name IS NULL RETURN n.id").records == [{"n.id": "n1"}]
    assert execute(graph, "MATCH (n:Person) WHERE n.name IS NOT NULL RETURN n.id").records == [{"n.id": "n2"}]


def test_cypher_node_scan_where_and_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", labels=["Person"], properties={"age": 40, "status": "active"}))
    graph.put_node(Node(node_id="n2", labels=["Person"], properties={"age": 40, "status": "inactive"}))

    result = execute(graph, 'MATCH (n:Person) WHERE n.age >= 35 AND n.status = "active" RETURN n.id')

    assert result.records == [{"n.id": "n1"}]


def test_cypher_traversal_where_filters_target_node_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1", properties={"kind": "protein", "name": "PTGS1"}))
    graph.put_node(Node(node_id="protein-2", properties={"kind": "protein", "name": "PTGS2"}))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="drug-1", target="protein-2", properties={"type": "T"}))

    result = execute(graph, 'MATCH (d {id: "drug-1"})-[:T]->(p) WHERE p.name = "PTGS2" RETURN p.id')

    assert result.records == [{"p.id": "protein-2"}]


def test_cypher_traversal_where_filters_relationship_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1"))
    graph.put_node(Node(node_id="protein-2"))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T", "score": 0.5}))
    graph.put_edge(Edge(edge_id="e2", source="drug-1", target="protein-2", properties={"type": "T", "score": 0.9}))

    result = execute(graph, 'MATCH (d {id: "drug-1"})-[r:T]->(p) WHERE r.score > $score RETURN r.id, p.id', parameters={"score": 0.7})

    assert result.records == [{"r.id": "e2", "p.id": "protein-2"}]


def test_cypher_traversal_where_and_limit_filters_before_limit_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1", properties={"name": "skip"}))
    graph.put_node(Node(node_id="protein-2", properties={"name": "keep"}))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T", "score": 1}))
    graph.put_edge(Edge(edge_id="e2", source="drug-1", target="protein-2", properties={"type": "T", "score": 1}))

    result = execute(
        graph,
        'MATCH (d {id: "drug-1"})-[r:T]->(p) WHERE r.score = 1 AND p.name = "keep" RETURN p.id LIMIT 1',
    )

    assert result.records == [{"p.id": "protein-2"}]


def test_cypher_traversal_where_rejects_unbound_variable_without_backend():
    with pytest.raises(ValueError, match="WHERE references unbound variable"):
        parse('MATCH (d {id: "drug-1"})-[:T]->(p) WHERE x.name = "PTGS1" RETURN p')


def test_cypher_traversal_supports_relationship_type_alternatives_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1"))
    graph.put_node(Node(node_id="disease-1"))
    graph.put_node(Node(node_id="ignored-1"))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"}))
    graph.put_edge(Edge(edge_id="e2", source="drug-1", target="disease-1", properties={"type": "drug-to-disease"}))
    graph.put_edge(Edge(edge_id="e3", source="drug-1", target="ignored-1", properties={"type": "ignored"}))

    result = execute(graph, 'MATCH (d {id: "drug-1"})-[:drug-to-protein|drug-to-disease]->(x) RETURN x.id')
    parsed = parse('MATCH (d {id: "drug-1"})-[:drug-to-protein|drug-to-disease]->(x) RETURN x.id')

    assert parsed.hops[0].edge_type == "drug-to-protein"
    assert parsed.hops[0].edge_types == ("drug-to-protein", "drug-to-disease")
    assert result.records == [{"x.id": "protein-1"}, {"x.id": "disease-1"}]


def test_cypher_reverse_traversal_supports_relationship_type_alternatives_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="target"))
    graph.put_node(Node(node_id="source-a"))
    graph.put_node(Node(node_id="source-b"))
    graph.put_edge(Edge(edge_id="e1", source="source-a", target="target", properties={"type": "A"}))
    graph.put_edge(Edge(edge_id="e2", source="source-b", target="target", properties={"type": "B"}))

    result = execute(graph, 'MATCH (t {id: "target"})<-[:A|B]-(source) RETURN source.id')

    assert result.records == [{"source.id": "source-a"}, {"source.id": "source-b"}]


def test_cypher_unanchored_relationship_scan_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1"))
    graph.put_node(Node(node_id="ignored-1"))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="drug-1", target="ignored-1", properties={"type": "ignored"}))

    result = execute(graph, "MATCH (a)-[r:T]->(b) RETURN a.id AS source, r.id AS rel, b.id AS target")
    parsed = parse("MATCH (a)-[r:T]->(b) RETURN a.id AS source, r.id AS rel, b.id AS target")

    assert parsed.edge_type == "T"
    assert result.records == [{"source": "drug-1", "rel": "e1", "target": "protein-1"}]


def test_cypher_unanchored_relationship_scan_type_alternatives_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_node(Node(node_id="c"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "A"}))
    graph.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "B"}))

    result = execute(graph, "MATCH (a)-[r:A|B]->(b) RETURN r.id ORDER BY r.id")

    assert result.records == [{"r.id": "e1"}, {"r.id": "e2"}]


def test_cypher_multiple_match_clauses_chain_shared_variables(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"name": "A"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"name": "B"}),
        Node(node_id="protein-1", labels=["Protein"], properties={"kind": "target"}),
        Node(node_id="protein-2", labels=["Protein"]),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T"}),
        Edge(edge_id="e2", source="drug-2", target="protein-2", properties={"type": "T"}),
    ])

    result = graph_db.query("MATCH (d:Drug {name: 'A'}) MATCH (d)-[r:T]->(p) RETURN d.id, r.id, p.id")
    parsed = parse("MATCH (d:Drug {name: 'A'}) MATCH (d)-[r:T]->(p) RETURN d.id, r.id, p.id")

    assert isinstance(parsed, MultiMatchQuery)
    assert result.records == [{"d.id": "drug-1", "r.id": "e1", "p.id": "protein-1"}]


def test_cypher_multiple_match_clauses_filter_bound_node_identity(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"]),
        Node(node_id="protein-1", labels=["Protein"], properties={"kind": "target"}),
        Node(node_id="protein-2", labels=["Protein"]),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T"}),
        Edge(edge_id="e2", source="drug-1", target="protein-2", properties={"type": "T"}),
    ])

    result = graph_db.query("MATCH (p:Protein {kind: 'target'}) MATCH (d)-[r:T]->(p) RETURN d.id, r.id, p.id")

    assert result.records == [{"d.id": "drug-1", "r.id": "e1", "p.id": "protein-1"}]


def test_cypher_multiple_match_clauses_support_where_order_limit(graph_db):
    graph_db.put_nodes([
        Node(node_id="d", labels=["Drug"]),
        Node(node_id="p1", properties={"rank": 2}),
        Node(node_id="p2", properties={"rank": 1}),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="d", target="p1", properties={"type": "T", "score": 0.5}),
        Edge(edge_id="e2", source="d", target="p2", properties={"type": "T", "score": 0.9}),
    ])

    result = graph_db.query("MATCH (d:Drug) MATCH (d)-[r:T]->(p) WHERE r.score > 0.6 RETURN p.id ORDER BY p.rank LIMIT 1")

    assert result.records == [{"p.id": "p2"}]


def test_cypher_multiple_match_clauses_support_anchored_clause(graph_db):
    graph_db.put_nodes([
        Node(node_id="d", labels=["Drug"]),
        Node(node_id="p"),
    ])
    graph_db.put_edge(Edge(edge_id="e", source="d", target="p", properties={"type": "T"}))

    result = graph_db.query("MATCH (d:Drug) MATCH (d {id: 'd'})-[r:T]->(p) RETURN d.id, r.id, p.id")

    assert result.records == [{"d.id": "d", "r.id": "e", "p.id": "p"}]


def test_cypher_unanchored_relationship_scan_where_and_return_star_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_node(Node(node_id="c"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T", "score": 0.5}))
    graph.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "T", "score": 0.9}))

    result = execute(graph, "MATCH (a)-[r:T]->(b) WHERE r.score > 0.7 RETURN *")

    assert result.columns == ("a", "r", "b")
    assert result.records[0]["r"].get_id == "e2"
    assert result.records[0]["b"].get_id == "c"


def test_cypher_unanchored_relationship_scan_where_equality_uses_edge_type_property_index(graph_db):
    graph_db.put_node(Node(node_id="a"))
    graph_db.put_node(Node(node_id="b"))
    graph_db.put_node(Node(node_id="c"))
    graph_db.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T", "score": 0.5}))
    graph_db.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "T", "score": 0.9}))
    graph_db.create_edge_property_index("score")
    original = graph_db.iter_edge_ids_by_type_property
    calls = []

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    graph_db.iter_edge_ids_by_type_property = tracked

    result = graph_db.query("MATCH (a)-[r:T]->(b) WHERE r.score = 0.9 RETURN r.id, b.id")

    assert calls == [(("T", "score", 0.9), {})]
    assert result.records == [{"r.id": "e2", "b.id": "c"}]


def test_cypher_unanchored_relationship_scan_where_range_uses_edge_type_property_range_index(graph_db):
    graph_db.put_node(Node(node_id="a"))
    graph_db.put_node(Node(node_id="b"))
    graph_db.put_node(Node(node_id="c"))
    graph_db.put_node(Node(node_id="d"))
    graph_db.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T", "score": 0.5}))
    graph_db.put_edge(Edge(edge_id="e2", source="a", target="c", properties={"type": "T", "score": 0.9}))
    graph_db.put_edge(Edge(edge_id="e3", source="a", target="d", properties={"type": "T", "score": 1.2}))
    graph_db.create_edge_property_index("score")
    original = graph_db.iter_edge_ids_by_type_property_range
    calls = []

    def tracked(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    graph_db.iter_edge_ids_by_type_property_range = tracked

    result = graph_db.query("MATCH (a)-[r:T]->(b) WHERE r.score > $score RETURN r.id", parameters={"score": 0.7})

    assert calls == [(("T", "score", 0.7, None, False, True), {})]
    assert result.records == [{"r.id": "e2"}, {"r.id": "e3"}]


def test_cypher_reverse_unanchored_relationship_scan_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="a"))
    graph.put_node(Node(node_id="b"))
    graph.put_edge(Edge(edge_id="e1", source="a", target="b", properties={"type": "T"}))

    result = execute(graph, "MATCH (b)<-[r:T]-(a) RETURN a.id, b.id, r.id")
    assert result.records == [{"a.id": "a", "b.id": "b", "r.id": "e1"}]


def test_cypher_node_scan_where_rejects_unbound_variable_without_backend():
    with pytest.raises(ValueError, match="WHERE references unbound variable"):
        parse('MATCH (n:Drug) WHERE m.name = "Aspirin" RETURN n')


def test_cypher_node_scan_where_rejects_unsupported_expression_without_backend():
    with pytest.raises(ValueError, match="Unsupported WHERE expression"):
        parse('MATCH (n:Drug) WHERE n.name STARTS WITH "A" RETURN n')


def test_cypher_label_scan_rejects_unbound_return_variable(graph_db):
    with pytest.raises(ValueError, match="unbound variable"):
        graph_db.query("MATCH (n:Drug) RETURN m")


def test_cypher_all_node_scan_returns_nodes(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"]),
        Node(node_id="protein-1", labels=["Protein"]),
    ])

    result = graph_db.query("MATCH (n) RETURN n.id")

    assert result.records == [{"n.id": "drug-1"}, {"n.id": "protein-1"}]


def test_cypher_all_node_scan_limit_stops_key_iteration_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_node(Node(node_id="n2"))
    graph.put_node(Node(node_id="n3"))

    result = execute(graph, "MATCH (n) RETURN n.id LIMIT 1")

    parsed = parse("MATCH (n) RETURN n.id LIMIT 1")


    assert parsed.label is None
    assert parsed.labels == ()
    assert result.records == [{"n.id": "n1"}]
    assert graph.node_key_yields == [b"n1"]


def test_cypher_all_node_scan_where_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", properties={"kind": "drug"}))
    graph.put_node(Node(node_id="n2", properties={"kind": "protein"}))

    result = execute(graph, 'MATCH (n) WHERE n.kind = "drug" RETURN n.id')

    assert result.records == [{"n.id": "n1"}]


def test_cypher_all_node_scan_where_parameter_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", properties={"age": 30}))
    graph.put_node(Node(node_id="n2", properties={"age": 40}))

    result = execute(graph, "MATCH (n) WHERE n.age > $age RETURN n.id", parameters={"age": 35})
    assert result.records == [{"n.id": "n2"}]


def test_cypher_all_node_scan_order_skip_limit_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", properties={"age": 30}))
    graph.put_node(Node(node_id="n2", properties={"age": 40}))
    graph.put_node(Node(node_id="n3", properties={"age": 20}))

    result = execute(graph, "MATCH (n) RETURN n.id ORDER BY n.age DESC SKIP 1 LIMIT 1")
    parsed = parse("MATCH (n) RETURN n.id ORDER BY n.age DESC SKIP 1 LIMIT 1")

    assert parsed.order_by[0].expression == "n.age"
    assert parsed.order_by[0].descending is True
    assert parsed.skip == 1
    assert result.records == [{"n.id": "n1"}]


def test_cypher_all_node_scan_return_distinct_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1", properties={"kind": "drug"}))
    graph.put_node(Node(node_id="n2", properties={"kind": "drug"}))
    graph.put_node(Node(node_id="n3", properties={"kind": "protein"}))

    result = execute(graph, "MATCH (n) RETURN DISTINCT n.kind ORDER BY n.kind")

    assert result.records == [{"n.kind": "drug"}, {"n.kind": "protein"}]


def test_cypher_all_node_scan_return_star_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))

    result = execute(graph, "MATCH (n) RETURN *")
    parsed = parse("MATCH (n) RETURN *")

    assert parsed.returns == ("n",)
    assert result.columns == ("n",)
    assert result.records[0]["n"].get_id == "n1"


def test_cypher_label_scan_can_project_properties(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
        Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}),
    ])

    result = graph_db.query("MATCH (n:Drug) RETURN n.id, n.name")

    assert result.columns == ("n.id", "n.name")
    assert result.records == [
        {"n.id": "drug-1", "n.name": "Aspirin"},
        {"n.id": "drug-2", "n.name": "Ibuprofen"},
    ]


def test_cypher_label_scan_can_alias_return_items(graph_db):
    graph_db.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin"}))

    result = graph_db.query("MATCH (n:Drug) RETURN n.id AS id, n.name AS name")
    parsed = parse("MATCH (n:Drug) RETURN n.id AS id, n.name AS name")

    assert parsed.returns == ("id", "name")
    assert parsed.projections == ("n.id", "n.name")
    assert result.columns == ("id", "name")
    assert result.records == [{"id": "drug-1", "name": "Aspirin"}]


def test_cypher_label_scan_limit_restricts_results(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"]),
        Node(node_id="drug-2", labels=["Drug"]),
        Node(node_id="drug-3", labels=["Drug"]),
    ])

    result = graph_db.query("MATCH (n:Drug) RETURN n.id LIMIT 2")

    assert result.columns == ("n.id",)
    assert result.records == [{"n.id": "drug-1"}, {"n.id": "drug-2"}]


def test_cypher_label_scan_limit_zero_returns_no_records(graph_db):
    graph_db.put_nodes([
        Node(node_id="drug-1", labels=["Drug"]),
        Node(node_id="drug-2", labels=["Drug"]),
    ])

    result = graph_db.query("MATCH (n:Drug) RETURN n.id LIMIT 0")

    assert result.columns == ("n.id",)
    assert result.records == []


def test_cypher_label_scan_limit_stops_index_iteration(graph_db):
    yielded = []

    def iter_node_ids_by_label(label):
        assert label == "Drug"
        for node_id in [b"drug-1", b"drug-2", b"drug-3"]:
            yielded.append(node_id)
            yield node_id

    graph_db.iter_node_ids_by_label = iter_node_ids_by_label
    graph_db.get_node = lambda node_id: Node(node_id=node_id.decode("utf-8"), labels=["Drug"])

    result = graph_db.query("MATCH (n:Drug) RETURN n.id LIMIT 1")

    assert result.records == [{"n.id": "drug-1"}]
    assert yielded == [b"drug-1"]


def test_cypher_label_scan_limit_stops_index_iteration_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"]))
    graph.put_node(Node(node_id="drug-2", labels=["Drug"]))
    graph.put_node(Node(node_id="drug-3", labels=["Drug"]))

    result = execute(graph, "MATCH (n:Drug) RETURN n.id LIMIT 1")

    assert result.records == [{"n.id": "drug-1"}]
    assert graph.label_yields == [b"drug-1"]


def test_cypher_id_projection_prefers_entity_identity(graph_db):
    graph_db.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"id": "shadow", "name": "Aspirin"}))

    result = graph_db.query("MATCH (n:Drug) RETURN n.id, n.name")

    assert result.records == [{"n.id": "drug-1", "n.name": "Aspirin"}]


def test_cypher_id_projection_prefers_entity_identity_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"id": "shadow", "name": "Aspirin"}))

    result = execute(graph, "MATCH (n:Drug) RETURN n.id, n.name")

    assert result.records == [{"n.id": "drug-1", "n.name": "Aspirin"}]


def test_cypher_one_hop_typed_match_returns_bound_nodes(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[:drug-to-protein]->(p) RETURN d, p')

    assert result.columns == ("d", "p")
    assert {record["d"].get_id for record in result} == {"drug-1"}
    assert {record["p"].get_id for record in result} == {"protein-1", "protein-2"}


def test_cypher_anchored_match_returns_empty_for_missing_source(graph_db):
    result = graph_db.query('MATCH (d {id: "missing"})-[:drug-to-protein]->(p) RETURN d, p')

    assert result.columns == ("d", "p")
    assert result.records == []


def test_cypher_anchored_match_skips_missing_target_node(graph_db):
    graph_db.put_node(Node(node_id="drug-1"))
    graph_db.store.put_typed_adjacency(b"drug-1", b"missing-protein", "drug-to-protein", b"e1")

    result = graph_db.query('MATCH (d {id: "drug-1"})-[:drug-to-protein]->(p) RETURN d, p')

    assert result.records == []


def test_cypher_one_hop_typed_match_can_bind_relationship(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[r:drug-to-disease]->(x) RETURN d, r, x')

    assert result.columns == ("d", "r", "x")
    assert len(result) == 1
    assert result.records[0]["r"].get_id == "d1-disease"
    assert result.records[0]["x"].get_id == "disease-1"


def test_cypher_typed_match_can_project_node_and_relationship_properties(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[r:drug-to-protein]->(p) RETURN d.id, r.type, p.kind')

    assert result.columns == ("d.id", "r.type", "p.kind")
    assert result.records == [
        {"d.id": "drug-1", "r.type": "drug-to-protein", "p.kind": "protein"},
        {"d.id": "drug-1", "r.type": "drug-to-protein", "p.kind": "protein"},
    ]


def test_cypher_typed_match_can_alias_return_items_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1"))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T", "score": 0.9}))

    result = execute(graph, 'MATCH (d {id: "drug-1"})-[r:T]->(p) RETURN r.score AS score, p.id AS protein')

    assert result.columns == ("score", "protein")
    assert result.records == [{"score": 0.9, "protein": "protein-1"}]


def test_cypher_typed_match_return_star_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1"))
    graph.put_node(Node(node_id="protein-1"))
    graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "T"}))

    result = execute(graph, 'MATCH (d {id: "drug-1"})-[r:T]->(p) RETURN *')
    parsed = parse('MATCH (d {id: "drug-1"})-[r:T]->(p) RETURN *')

    assert parsed.returns == ("d", "r", "p")
    assert result.columns == ("d", "r", "p")
    assert result.records[0]["d"].get_id == "drug-1"
    assert result.records[0]["r"].get_id == "e1"
    assert result.records[0]["p"].get_id == "protein-1"


def test_cypher_typed_match_limit_restricts_results(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[:drug-to-protein]->(p) RETURN p.id LIMIT 1')

    assert result.columns == ("p.id",)
    assert result.records == [{"p.id": "protein-1"}]


def test_cypher_typed_match_limit_zero_returns_no_records(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[:drug-to-protein]->(p) RETURN p.id LIMIT 0')

    assert result.columns == ("p.id",)
    assert result.records == []


def test_cypher_reused_node_variable_requires_same_node(graph_db):
    graph_db.put_nodes([
        Node(node_id="n1"),
        Node(node_id="n2"),
        Node(node_id="n3"),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}),
        Edge(edge_id="e2", source="n2", target="n2", properties={"type": "T"}),
        Edge(edge_id="e3", source="n2", target="n3", properties={"type": "T"}),
    ])

    result = graph_db.query('MATCH (a {id: "n1"})-[:T]->(b)-[:T]->(b) RETURN b.id')

    assert result.records == [{"b.id": "n2"}]


def test_cypher_reused_node_variable_requires_same_node_without_backend():
    graph = FakeCypherGraph()
    for node_id in ["n1", "n2", "n3"]:
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="n2", target="n2", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e3", source="n2", target="n3", properties={"type": "T"}))

    result = execute(graph, 'MATCH (a {id: "n1"})-[:T]->(b)-[:T]->(b) RETURN b.id')

    assert result.records == [{"b.id": "n2"}]


def test_cypher_reused_relationship_variable_requires_same_edge(graph_db):
    graph_db.put_nodes([
        Node(node_id="n1"),
        Node(node_id="n2"),
        Node(node_id="n3"),
    ])
    graph_db.put_edges_bulk([
        Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}),
        Edge(edge_id="e2", source="n2", target="n3", properties={"type": "T"}),
    ])

    result = graph_db.query('MATCH (a {id: "n1"})-[r:T]->(b)-[r:T]->(c) RETURN r.id, c.id')

    assert result.records == []


def test_cypher_reused_relationship_variable_requires_same_edge_without_backend():
    graph = FakeCypherGraph()
    for node_id in ["n1", "n2", "n3"]:
        graph.put_node(Node(node_id=node_id))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="n2", target="n3", properties={"type": "T"}))

    result = execute(graph, 'MATCH (a {id: "n1"})-[r:T]->(b)-[r:T]->(c) RETURN r.id, c.id')

    assert result.records == []


def test_cypher_self_loop_traversal(graph_db):
    graph_db.put_node(Node(node_id="n1"))
    graph_db.put_edge(Edge(edge_id="self", source="n1", target="n1", properties={"type": "T"}))

    result = graph_db.query('MATCH (a {id: "n1"})-[:T]->(b) RETURN a.id, b.id')

    assert result.records == [{"a.id": "n1", "b.id": "n1"}]


def test_cypher_multi_hop_typed_match_returns_bound_nodes(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[:drug-to-protein]->(p)-[:protein-to-disease]->(x) RETURN d, p, x')

    assert result.columns == ("d", "p", "x")
    assert {record["d"].get_id for record in result} == {"drug-1"}
    assert {(record["p"].get_id, record["x"].get_id) for record in result} == {
        ("protein-1", "disease-1"),
        ("protein-1", "disease-2"),
        ("protein-2", "disease-3"),
    }


def test_cypher_multi_hop_typed_match_can_bind_relationships(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (d {id: "drug-1"})-[r1:drug-to-protein]->(p)-[r2:protein-to-disease]->(x) RETURN r1, r2, x')

    assert result.columns == ("r1", "r2", "x")
    assert {(record["r1"].get_type, record["r2"].get_type, record["x"].get_id) for record in result} == {
        ("drug-to-protein", "protein-to-disease", "disease-1"),
        ("drug-to-protein", "protein-to-disease", "disease-2"),
        ("drug-to-protein", "protein-to-disease", "disease-3"),
    }


def test_cypher_reverse_typed_match_returns_bound_nodes(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (p {id: "protein-1"})<-[:drug-to-protein]-(d) RETURN p, d')

    assert result.columns == ("p", "d")
    assert len(result) == 1
    assert result.records[0]["p"].get_id == "protein-1"
    assert result.records[0]["d"].get_id == "drug-1"


def test_cypher_reverse_typed_match_can_bind_relationship(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (p {id: "protein-1"})<-[r:drug-to-protein]-(d) RETURN r, d')

    assert result.columns == ("r", "d")
    assert len(result) == 1
    assert result.records[0]["r"].get_id == "d1-p1"
    assert result.records[0]["d"].get_id == "drug-1"


def test_cypher_undirected_typed_match_returns_both_directions(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query('MATCH (p {id: "protein-1"})-[:drug-to-protein]-(n) RETURN n')

    assert [record["n"].get_id for record in result] == ["drug-1"]


def test_cypher_mixed_direction_multi_hop_match(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query(
        'MATCH (x {id: "disease-1"})<-[:protein-to-disease]-(p)<-[:drug-to-protein]-(d) RETURN x, p, d'
    )

    assert result.columns == ("x", "p", "d")
    assert [(record["x"].get_id, record["p"].get_id, record["d"].get_id) for record in result] == [
        ("disease-1", "protein-1", "drug-1")
    ]


def test_cypher_sample_typed_paths_call_returns_paths(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query(
        'CALL pg.sample_typed_paths(["drug-1"], '
        '[{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}, '
        '{"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1}]) '
        'YIELD path RETURN path'
    )

    assert result.columns == ("path",)
    assert result.records
    for record in result:
        sampled_path = record["path"]
        assert sampled_path["seed"] == b"drug-1"
        assert len(sampled_path["path"]) == 2
        assert sampled_path["path"][0]["edge_type"] == "drug-to-protein"
        assert sampled_path["path"][1]["edge_type"] == "protein-to-disease"


def test_cypher_sample_typed_paths_call_supports_limit(graph_db):
    populate_typed_graph(graph_db)

    result = graph_db.query(
        'CALL pg.sample_typed_paths(["drug-1"], '
        '[{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}]) '
        'YIELD path RETURN path LIMIT 1'
    )

    assert result.columns == ("path",)
    assert len(result) == 1


def test_cypher_sample_typed_paths_validates_arguments(graph_db):
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        graph_db.query('CALL pg.sample_typed_paths(["drug-1"], []) RETURN path')
    with pytest.raises(ValueError, match="seed IDs"):
        graph_db.query('CALL pg.sample_typed_paths("drug-1", []) YIELD path RETURN path')
    with pytest.raises(ValueError, match="pattern"):
        graph_db.query('CALL pg.sample_typed_paths(["drug-1"], {}) YIELD path RETURN path')
    with pytest.raises(ValueError, match="expects seed IDs"):
        graph_db.query('CALL pg.sample_typed_paths(["drug-1"]) YIELD path RETURN path')


def test_cypher_parser_rejects_malformed_match_queries(graph_db):
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        graph_db.query("RETURN n")
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        graph_db.query("MATCH () RETURN n")
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        graph_db.query('MATCH (d {id: "drug-1"})-[:rel]->(p)')
    with pytest.raises(ValueError, match="unbound variable"):
        graph_db.query('MATCH (d {id: "drug-1"})-[:rel]->(p) RETURN missing')


def test_cypher_parser_rejects_invalid_property_literal_with_clear_error(graph_db):
    with pytest.raises(ValueError, match="Invalid Cypher literal"):
        graph_db.query("MATCH (n:Drug {name: nope}) RETURN n")


def test_split_top_level_args_handles_nested_strings_and_escapes():
    args = _split_top_level_args('["drug,1"], [{"edge_type": "a,\\\"b", "sample_size": 1}]')

    assert args == ['["drug,1"]', '[{"edge_type": "a,\\\"b", "sample_size": 1}]']


def test_query_result_iterates_records():
    result = QueryResult(columns=("n",), records=[{"n": "node"}])

    assert len(result) == 1
    assert list(result) == [{"n": "node"}]


def test_parse_node_scan_returns_none_for_non_node_scan():
    parsed = parse('MATCH (d {id: "drug-1"})-[:rel]->(p) RETURN d, p')

    assert parsed.source_var == "d"


def test_parse_node_scan_parameter_value():
    parsed = parse("MATCH (n:Drug {name: $name}) RETURN n")

    assert parsed.property_value == Parameter("name")


def test_parse_literal_supports_cypher_literals_and_parameters():
    assert parse_literal("true") is True
    assert parse_literal("false") is False
    assert parse_literal("null") is None
    assert parse_literal("1") == 1
    assert parse_literal("1.5") == 1.5
    assert parse_literal('["a", "b"]') == ["a", "b"]
    assert parse_literal('{"name": "Aspirin"}') == {"name": "Aspirin"}
    assert parse_literal("$name") == Parameter("name")


def test_logical_plan_for_node_scan():
    logical_plan = plan("MATCH (n:Drug {name: $name}) RETURN n.id LIMIT 1")

    assert isinstance(logical_plan.operators[0], NodeLabelScan)
    assert isinstance(logical_plan.operators[1], NodePropertySeek)
    assert isinstance(logical_plan.operators[-2], Project)
    assert isinstance(logical_plan.operators[-1], Limit)


def test_logical_plan_for_node_scan_where():
    logical_plan = plan('MATCH (n:Drug) WHERE n.name = "Aspirin" RETURN n.id')

    assert isinstance(logical_plan.operators[0], NodeLabelScan)
    assert isinstance(logical_plan.operators[-1], Project)


def test_logical_plan_for_multi_label_node_scan():
    logical_plan = plan("MATCH (n:Drug:Approved) RETURN n.id")

    assert logical_plan.operators[0].labels == ("Drug", "Approved")


def test_logical_plan_for_all_node_scan():
    logical_plan = plan("MATCH (n) RETURN n.id LIMIT 1")

    assert isinstance(logical_plan.operators[0], NodeAllScan)
    assert isinstance(logical_plan.operators[-1], Limit)


def test_logical_plan_for_anchored_traversal():
    logical_plan = plan('MATCH (a {id: "n1"})-[:T]->(b) RETURN b.id')

    assert isinstance(logical_plan.operators[0], NodeByIdSeek)
    assert isinstance(logical_plan.operators[1], Expand)
    assert isinstance(logical_plan.operators[-1], Project)


def test_logical_plan_for_sampling_call():
    logical_plan = plan(
        'CALL pg.sample_typed_paths(["n1"], [{"edge_type": "T", "sample_size": 1}]) YIELD path RETURN path LIMIT 1'
    )

    assert isinstance(logical_plan.operators[0], ProcedureCall)
    assert isinstance(logical_plan.operators[-1], Limit)


def test_logical_plan_for_relationship_scan():
    logical_plan = plan("MATCH (a)-[r:A|B]->(b) RETURN r.id")

    assert isinstance(logical_plan.operators[0], RelationshipTypeScan)
    assert logical_plan.operators[0].edge_types == ("A", "B")


def test_logical_plan_for_relationship_scan_property_predicates():
    exact_plan = plan("MATCH (a)-[r:T]->(b) WHERE r.score = 1 RETURN r.id")
    range_plan = plan("MATCH (a)-[r:T]->(b) WHERE r.score >= 1 RETURN r.id")

    assert isinstance(exact_plan.operators[1], RelationshipPropertySeek)
    assert exact_plan.operators[1].property_name == "score"
    assert isinstance(range_plan.operators[1], RelationshipPropertyRangeSeek)
    assert range_plan.operators[1].operator == ">="


def test_logical_plan_for_multiple_match_clauses():
    logical_plan = plan("MATCH (d:Drug) MATCH (d)-[r:T]->(p) RETURN d.id, p.id LIMIT 1")

    assert isinstance(logical_plan.operators[0], NodeLabelScan)
    assert isinstance(logical_plan.operators[1], RelationshipTypeScan)
    assert isinstance(logical_plan.operators[-2], Project)
    assert isinstance(logical_plan.operators[-1], Limit)


def test_query_context_caches_node_and_edge_hydration_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n1", properties={"type": "T"}))
    node_gets = 0
    edge_gets = 0
    original_get_node = graph.get_node
    original_get_edge = graph.get_edge

    def get_node(node_id):
        nonlocal node_gets
        node_gets += 1
        return original_get_node(node_id)

    def get_edge(edge_id):
        nonlocal edge_gets
        edge_gets += 1
        return original_get_edge(edge_id)

    graph.get_node = get_node
    graph.get_edge = get_edge
    context = QueryContext(graph)

    assert context.get_node(b"n1") is context.get_node(b"n1")
    assert context.get_edge(b"e1") is context.get_edge(b"e1")
    assert node_gets == 1
    assert edge_gets == 1


def test_runtime_node_scan_streams_until_limit_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="drug-1", labels=["Drug"]))
    graph.put_node(Node(node_id="drug-2", labels=["Drug"]))
    parsed = parse("MATCH (n:Drug) RETURN n.id LIMIT 1")

    records = execute_node_scan(parsed, QueryContext(graph))

    assert records == [{"n.id": "drug-1"}]
    assert graph.label_yields == [b"drug-1"]


def test_runtime_expand_operator_binds_relationships_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_node(Node(node_id="n2"))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}))
    parsed = parse('MATCH (a {id: "n1"})-[r:T]->(b) RETURN r.id, b.id')
    context = QueryContext(graph)
    rows = [{"current_node_id": b"n1", "bindings": {"a": graph.get_node(b"n1")}}]

    expanded = list(expand_typed(context, rows, parsed.hops[0]))

    assert len(expanded) == 1
    assert expanded[0]["bindings"]["r"].get_id == "e1"
    assert expanded[0]["bindings"]["b"].get_id == "n2"


def test_runtime_match_operator_respects_limit_without_backend():
    graph = FakeCypherGraph()
    graph.put_node(Node(node_id="n1"))
    graph.put_node(Node(node_id="n2"))
    graph.put_node(Node(node_id="n3"))
    graph.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "T"}))
    graph.put_edge(Edge(edge_id="e2", source="n1", target="n3", properties={"type": "T"}))
    parsed = parse('MATCH (a {id: "n1"})-[:T]->(b) RETURN b.id LIMIT 1')

    records = execute_match(parsed, QueryContext(graph))

    assert records == [{"b.id": "n2"}]


def test_cypher_unsupported_query_raises_clear_error(graph_db):
    with pytest.raises(ValueError, match="Unsupported Cypher query"):
        graph_db.query("MATCH (n)-->(m) RETURN n")
