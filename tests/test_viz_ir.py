"""Tests for the VIZ-01 visualization intermediate representation."""

from __future__ import annotations

import json

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.ir import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    VIZ_IR_VERSION,
    VizGraph,
)


def _nodes_edges():
    nodes = [
        Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}),
        Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}),
        Node(node_id="acme", labels=["Company"], properties={"name": "Acme"}),
    ]
    edges = [
        Edge(edge_id="e2", source="bob", target="acme", properties={"type": "works_for"}),
        Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows", "since": 2024}),
    ]
    return nodes, edges


def test_from_nodes_edges_deterministic_ordering():
    nodes, edges = _nodes_edges()
    first = VizGraph.from_nodes_edges(nodes, edges)
    second = VizGraph.from_nodes_edges(list(reversed(nodes)), list(reversed(edges)))
    assert first.to_json() == second.to_json()
    assert first.node_ids == ("acme", "alice", "bob")
    assert first.edge_ids == ("e1", "e2")
    assert first.to_dict()["version"] == VIZ_IR_VERSION
    assert first.truncation.truncated is False


def test_from_nodes_edges_caps_are_deterministic():
    nodes = [Node(node_id=f"n{i:03d}") for i in range(10)]
    edges = [Edge(edge_id=f"e{i:03d}", source=f"n{i:03d}", target=f"n{(i + 1) % 10:03d}") for i in range(10)]
    viz = VizGraph.from_nodes_edges(nodes, edges, max_nodes=4, max_edges=2)
    assert viz.node_ids == ("n000", "n001", "n002", "n003")
    assert viz.truncation.nodes_dropped == 6
    # Only edges fully inside the kept node set survive, then the edge cap.
    assert viz.truncation.edges_dropped == 8
    assert len(viz.edges) == 2
    assert viz.truncation.truncated is True


def test_edge_to_dropped_node_counts_as_dropped():
    nodes = [Node(node_id="a"), Node(node_id="b"), Node(node_id="c")]
    edges = [Edge(edge_id="e1", source="a", target="zzz-missing")]
    viz = VizGraph.from_nodes_edges(nodes, edges)
    assert viz.edge_ids == ()
    assert viz.truncation.edges_dropped == 1


def test_malicious_strings_round_trip():
    evil = '</script><script>alert("x")</script>\u2028\u2029'
    nodes = [Node(node_id=evil, labels=[evil], properties={"p": evil})]
    edges = [Edge(edge_id=evil, source=evil, target=evil, properties={"type": evil})]
    viz = VizGraph.from_nodes_edges(nodes, edges)
    payload = json.loads(viz.to_json())
    assert payload["nodes"][0]["id"] == evil
    assert payload["edges"][0]["type"] == evil


def test_property_sanitization():
    nodes = [
        {
            "id": "n1",
            "labels": ["L"],
            "properties": {
                "ok": 1,
                "blob": b"\xff\xfe-binary",
                "nested": {"a": [1, b"x"]},
                "nan": float("nan"),
            },
        }
    ]
    viz = VizGraph.from_nodes_edges(nodes, [])
    props = viz.to_dict()["nodes"][0]["properties"]
    assert props["ok"] == 1
    assert isinstance(props["blob"], str)
    assert props["nested"] == {"a": [1, "x"]}
    assert isinstance(props["nan"], str)


def test_from_cypher_result_entities_and_highlight(tmp_path):
    graph = GraphDB(LevelDBStore(path=str(tmp_path / "g")), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows"}))
        result = graph.query("MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b")
        viz = VizGraph.from_cypher_result(result)
    finally:
        graph.close()
    assert viz.node_ids == ("alice", "bob")
    assert viz.edge_ids == ("e1",)
    assert set(viz.highlight_nodes) == {"alice", "bob"}
    assert tuple(viz.highlight_edges) == ("e1",)
    assert viz.meta["columns"] == ["a", "r", "b"]


def test_from_cypher_result_scalar_only_is_explainable(tmp_path):
    graph = GraphDB(LevelDBStore(path=str(tmp_path / "g")), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        result = graph.query('MATCH (a:Person) RETURN a.name AS name')
        viz = VizGraph.from_cypher_result(result)
    finally:
        graph.close()
    assert viz.nodes == ()
    assert viz.edges == ()
    assert "scalar-only" in viz.meta["note"]


def test_from_sampled_subgraph_placeholders_and_highlight(tmp_path):
    graph = GraphDB(LevelDBStore(path=str(tmp_path / "g")), PickleSerializer())
    try:
        graph.put_node(Node(node_id="drug-1", properties={"kind": "drug"}))
        graph.put_node(Node(node_id="protein-1", properties={"kind": "protein"}))
        graph.put_edge(Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "binds"}))
        subgraph = graph.sample_typed_subgraph(["drug-1"], [{"edge_type": "binds", "direction": "out", "sample_size": 5}])
        # Simulate a missing record: the path still references the endpoint.
        subgraph["nodes"] = {key: value for key, value in subgraph["nodes"].items() if value is not None}
        viz = VizGraph.from_sampled_subgraph(subgraph)
    finally:
        graph.close()
    assert set(viz.node_ids) == {"drug-1", "protein-1"}
    assert viz.edge_ids == ("d1-p1",)
    assert set(viz.highlight_nodes) == {"drug-1", "protein-1"}
    assert viz.meta["path_count"] == 1


def test_from_sampler_batch_maps_local_to_external(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("numpy")
    from gestaltdb.sampling import SamplerEngine

    graph = GraphDB(LevelDBStore(path=str(tmp_path / "g")), PickleSerializer())
    try:
        graph.put_nodes(
            [
                Node(node_id="drug-1", properties={"kind": "drug"}),
                Node(node_id="protein-1", properties={"kind": "protein"}),
                Node(node_id="disease-1", properties={"kind": "disease"}),
            ]
        )
        graph.put_edges_bulk(
            [
                Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "binds"}),
                Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "associated_with"}),
            ]
        )
        snapshot = graph.build_sampler_snapshot(str(tmp_path / "snap"))
        engine = SamplerEngine.load(snapshot.path, mode="ram", seed=13)
        seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")
        batch = engine.sample_subgraph([seed_edge], fanouts=[5])
        viz = VizGraph.from_sampler_batch(batch, snapshot)
    finally:
        graph.close()
    assert set(viz.node_ids) >= {"drug-1", "protein-1"}
    assert viz.meta["builder"] == "from_sampler_batch"
    assert viz.to_json()  # canonical encoding never fails


def test_ir_module_has_no_third_party_runtime_dependency():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/gestaltdb/viz/ir.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "json", "math", "dataclasses", "typing"}


def test_default_caps_match_epic():
    assert (DEFAULT_MAX_NODES, DEFAULT_MAX_EDGES) == (2000, 5000)
