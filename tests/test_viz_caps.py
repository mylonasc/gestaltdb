"""Tests for VIZ-07 large-graph caps, warnings, ceilings, and batch path."""

from __future__ import annotations

import warnings

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import VizOptions, visualize_nodes_edges, visualize_sampler_batch
from gestaltdb.viz.ir import TruncationWarning, VizCapExceededError, VizGraph


def test_truncation_warns_with_actionable_message():
    nodes = [Node(node_id=f"n{i:03d}") for i in range(6)]
    with pytest.warns(TruncationWarning, match="visualize_sample"):
        figure = visualize_nodes_edges(nodes, [], options=VizOptions(max_nodes=4))
    assert figure.viz.truncation.nodes_dropped == 2
    assert "truncated" in repr(figure)


def test_no_warning_within_caps(recwarn):
    nodes = [Node(node_id="a")]
    visualize_nodes_edges(nodes, [])
    assert [w for w in recwarn.list if issubclass(w.category, TruncationWarning)] == []


def test_absolute_ceiling_raises_with_sampling_guidance():
    nodes = [Node(node_id=f"n{i}") for i in range(10)]
    with pytest.raises(VizCapExceededError, match="visualize_sample"):
        VizGraph.from_nodes_edges(nodes, [], max_nodes=4, absolute_max_nodes=5)


def test_default_ceiling_is_twice_the_cap():
    nodes = [Node(node_id=f"n{i:04d}") for i in range(9)]
    with pytest.raises(VizCapExceededError, match="absolute ceiling 8"):
        VizGraph.from_nodes_edges(nodes, [], max_nodes=4)
    ok_nodes = [Node(node_id=f"n{i:04d}") for i in range(8)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", TruncationWarning)
        viz = VizGraph.from_nodes_edges(ok_nodes, [], max_nodes=4)
    assert viz.truncation.nodes_dropped == 4


def test_edge_ceiling_counts_pre_cap_edges():
    nodes = [Node(node_id="a"), Node(node_id="b")]
    edges = [Edge(edge_id=f"e{i}", source="a", target="b") for i in range(12)]
    with pytest.raises(VizCapExceededError, match="12.*edges"):
        VizGraph.from_nodes_edges(nodes, edges, max_edges=5)


def test_options_absolute_overrides():
    nodes = [Node(node_id=f"n{i}") for i in range(10)]
    with pytest.raises(VizCapExceededError, match="absolute ceiling 9"):
        visualize_nodes_edges(nodes, [], options=VizOptions(max_nodes=4, absolute_max_nodes=9))


def test_large_fixture_truncates_deterministically():
    nodes = [{"id": f"n{i:06d}"} for i in range(100_000)]
    edges = [{"id": f"e{i:06d}", "source": f"n{i:06d}", "target": f"n{(i + 1) % 100_000:06d}"} for i in range(100_000)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", TruncationWarning)
        first = VizGraph.from_nodes_edges(
            nodes, edges, absolute_max_nodes=200_000, absolute_max_edges=200_000
        )
        second = VizGraph.from_nodes_edges(
            list(reversed(nodes)), list(reversed(edges)),
            absolute_max_nodes=200_000, absolute_max_edges=200_000,
        )
    assert first.to_json() == second.to_json()
    assert first.truncation.truncated is True
    assert len(first.nodes) == 2000
    # Ring edges crossing the keep boundary drop with their endpoint.
    assert len(first.edges) == 1999
    assert first.truncation.edges_dropped == 100_000 - 1999


def test_visualize_sampler_batch_smoke(tmp_path):
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
        figure = visualize_sampler_batch(batch, snapshot)
    finally:
        graph.close()
    assert set(figure.viz.node_ids) >= {"drug-1", "protein-1"}
    assert figure.viz.meta["builder"] == "from_sampler_batch"
