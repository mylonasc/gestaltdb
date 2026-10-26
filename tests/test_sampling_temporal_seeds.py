"""Sampling roadmap (#106): temporal seed selection."""

import pytest

from gestaltdb import TemporalContext
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import SamplerEngine
from gestaltdb.serializers import PickleSerializer
from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        yield db
    finally:
        db.close()


def build_temporal_snapshot(graph, path):
    graph.temporal.commit_versions([
        NodeVersionWrite.assertion(Node("a", properties={"kind": "x"}), (0, 10)),
        NodeVersionWrite.assertion(Node("b", properties={"kind": "x"}), (10, 20)),
        NodeVersionWrite.assertion(Node("c", properties={"kind": "y"}), (20, None)),
    ])
    for edge, valid in (
        (Edge("old", "a", "a", properties={"type": "R"}), (0, 10)),
        (Edge("new", "b", "b", properties={"type": "R"}), (10, 20)),
        (Edge("open", "c", "c", properties={"type": "S"}), (20, None)),
    ):
        graph.temporal.put_edge_version(edge, valid=valid)
    return graph.build_sampler_snapshot(path, temporal=True, time_bucket="none")


def external_names(snapshot, ids, attribute):
    return sorted(getattr(snapshot, attribute)[ids].tolist())


def test_temporal_seed_nodes_filter_point_and_window(graph, tmp_path):
    snapshot = build_temporal_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=1)

    assert external_names(
        snapshot,
        engine.sample_nodes(10, temporal=TemporalContext.as_of(5)),
        "external_node_ids",
    ) == ["a"]
    assert external_names(
        snapshot,
        engine.sample_nodes(10, temporal=TemporalContext.as_of(10)),
        "external_node_ids",
    ) == ["b"]
    assert external_names(
        snapshot,
        engine.sample_nodes(10, temporal=TemporalContext.as_of(20)),
        "external_node_ids",
    ) == ["c"]
    assert external_names(
        snapshot,
        engine.sample_nodes(10, temporal=TemporalContext.during(5, 15)),
        "external_node_ids",
    ) == ["a", "b"]
    assert external_names(
        snapshot,
        engine.sample_nodes(
            10,
            temporal=TemporalContext.during(5, 15),
            window_policy="contained",
        ),
        "external_node_ids",
    ) == []
    assert external_names(
        snapshot,
        engine.sample_nodes(
            10,
            temporal=TemporalContext.during(0, 20),
            window_policy="contained",
        ),
        "external_node_ids",
    ) == ["a", "b"]


def test_temporal_seed_nodes_combine_type_and_balanced_filters(graph, tmp_path):
    snapshot = build_temporal_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=3)
    sampled = engine.sample_nodes(
        2,
        temporal=TemporalContext.during(5, 25),
        balanced=True,
    )
    names = external_names(snapshot, sampled, "external_node_ids")
    assert len(names) == 2
    # Types are x={a,b} and y={c}; the balanced draw keeps one from each.
    assert "c" in names
    assert len(set(snapshot.node_type_ids[sampled].tolist())) == 2


def test_temporal_seed_edges_filter_point_and_window(graph, tmp_path):
    snapshot = build_temporal_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=5)

    assert external_names(
        snapshot,
        engine.sample_edges(10, temporal=TemporalContext.as_of(5)),
        "external_edge_ids",
    ) == ["old"]
    assert external_names(
        snapshot,
        engine.sample_edges(10, temporal=TemporalContext.as_of(10)),
        "external_edge_ids",
    ) == ["new"]
    assert external_names(
        snapshot,
        engine.sample_edges(10, temporal=TemporalContext.as_of(20)),
        "external_edge_ids",
    ) == ["open"]
    assert external_names(
        snapshot,
        engine.sample_edges(10, temporal=TemporalContext.during(5, 15)),
        "external_edge_ids",
    ) == ["new", "old"]
    assert external_names(
        snapshot,
        engine.sample_edges(
            10,
            temporal=TemporalContext.during(5, 15),
            window_policy="contained",
        ),
        "external_edge_ids",
    ) == []
    assert external_names(
        snapshot,
        engine.sample_edges(
            10,
            temporal=TemporalContext.during(0, 20),
            window_policy="contained",
        ),
        "external_edge_ids",
    ) == ["new", "old"]


def test_temporal_seed_edges_combine_relation_filter(graph, tmp_path):
    snapshot = build_temporal_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=7)
    relation = snapshot.external_relation_ids.tolist().index("S")

    assert engine.sample_edges(
        10,
        relations=[relation],
        temporal=TemporalContext.as_of(5),
    ).tolist() == []
    assert external_names(
        snapshot,
        engine.sample_edges(
            10,
            relations=[relation],
            temporal=TemporalContext.as_of(20),
        ),
        "external_edge_ids",
    ) == ["open"]


def test_temporal_seed_filters_fail_closed_without_temporal_history(graph, tmp_path):
    graph.put_node(Node("n", properties={"kind": "x"}))
    graph.put_edge(Edge("e", "n", "n", properties={"type": "R"}))
    snapshot = graph.build_sampler_snapshot(tmp_path / "plain")
    engine = SamplerEngine(snapshot)

    with pytest.raises(ValueError, match="format-v2 temporal"):
        engine.sample_nodes(1, temporal=TemporalContext.as_of(0))
    with pytest.raises(ValueError, match="format-v2 temporal"):
        engine.sample_edges(1, temporal=TemporalContext.as_of(0))
    with pytest.raises(ValueError, match="window_policy"):
        engine.sample_nodes(1, window_policy="touches")
    with pytest.raises(TypeError, match="TemporalContext"):
        engine.sample_nodes(1, temporal=object())
