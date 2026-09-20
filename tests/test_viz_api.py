"""Tests for the VIZ-06 public Python API and Jupyter rendering."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import (
    VizFigure,
    VizOptions,
    visualize_nodes_edges,
    visualize_query,
    visualize_sample,
)


@pytest.fixture
def small_graph(tmp_path):
    graph = GraphDB(LevelDBStore(path=str(tmp_path / "g")), PickleSerializer())
    graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
    graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
    graph.put_edge(Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows"}))
    try:
        yield graph
    finally:
        graph.close()


def test_options_validation():
    with pytest.raises(ValueError):
        VizOptions(theme="neon")
    with pytest.raises(ValueError):
        VizOptions(max_nodes=0)
    with pytest.raises(ValueError):
        VizOptions(height=-1)


def test_visualize_nodes_edges_save_equals_as_html(small_graph, tmp_path):
    nodes = [small_graph.get_node(b"alice"), small_graph.get_node(b"bob")]
    edges = [small_graph.get_edge(b"e1")]
    figure = visualize_nodes_edges(nodes, edges, options=VizOptions(title="T"))
    target = tmp_path / "graph.html"
    saved = figure.save(target)
    assert saved.read_text(encoding="utf-8") == figure.as_html()
    assert "window.__GESTALTDB_VIZ_OPTIONS__" in figure.as_html()
    assert repr(figure) == "VizFigure(nodes=2, edges=1)"


def test_repr_html_uses_srcdoc_iframe_without_external_requests():
    figure = visualize_nodes_edges([], [])
    snippet = figure._repr_html_()
    assert snippet.startswith("<iframe srcdoc=\"")
    assert 'height="600"' in snippet
    match = re.search(r'srcdoc="(.*)" width', snippet, re.DOTALL)
    assert match is not None
    import html as html_module

    document = html_module.unescape(match.group(1))
    head, _, _ = document.partition("window.__GESTALTDB_VIZ__")
    assert "http://" not in head and "https://" not in head


def test_view_overrides_round_trip_into_payload():
    figure = visualize_nodes_edges([], [], options=VizOptions(theme="dark", charge=-100, link_distance=80))
    match = re.search(r"window\.__GESTALTDB_VIZ_OPTIONS__ = (.*?);</script>", figure.as_html(), re.DOTALL)
    assert match is not None
    overrides = json.loads(match.group(1))
    assert overrides == {
        "theme": "dark",
        "charge": -100,
        "linkDistance": 80,
        "showLabels": True,
        "showProperties": True,
    }


def test_visualize_query_and_graph_method(small_graph, tmp_path):
    figure = visualize_query(small_graph, "MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b")
    assert figure.viz.node_ids == ("alice", "bob")
    via_method = small_graph.visualize("MATCH (a:Person) RETURN a LIMIT 5")
    assert isinstance(via_method, VizFigure)
    assert via_method.viz.node_ids == ("alice", "bob")
    with pytest.raises(ValueError):
        small_graph.visualize()
    with pytest.raises(ValueError):
        small_graph.visualize("MATCH (n) RETURN n", seeds=["alice"])


def test_visualize_sample_and_graph_method(small_graph):
    figure = visualize_sample(small_graph, ["alice"], [{"edge_type": "knows", "direction": "out", "sample_size": 5}])
    assert "alice" in figure.viz.node_ids
    via_method = small_graph.visualize(
        seeds=["alice"], pattern=[{"edge_type": "knows", "direction": "out", "sample_size": 5}]
    )
    assert via_method.viz.edge_ids == ("e1",)
    with pytest.raises(ValueError):
        small_graph.visualize(seeds=["alice"])


def test_options_accept_mapping():
    figure = visualize_nodes_edges([], [], options={"title": "Mapped", "theme": "dark"})
    assert figure.options.title == "Mapped"
    assert figure.options.theme == "dark"


def test_viz_is_lazily_imported():
    completed = subprocess.run(
        [sys.executable, "-c", "import gestaltdb, sys; print('gestaltdb.viz' in sys.modules)"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "False"
