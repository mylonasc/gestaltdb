"""Sampling roadmap (#106): bounded node-induced subgraphs."""

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


def build_plain_snapshot(graph, path):
    graph.put_nodes([Node(f"n{index}") for index in range(6)])
    for edge_id, src, dst, relation in (
        ("e0", "n0", "n1", "R"),
        ("e1", "n1", "n2", "R"),
        ("e2", "n2", "n3", "S"),
        ("e3", "n3", "n4", "R"),
        ("e4", "n4", "n5", "S"),
        ("e5", "n5", "n0", "R"),
    ):
        graph.put_edge(Edge(edge_id, src, dst, properties={"type": relation}))
    return graph.build_sampler_snapshot(path)


def build_temporal_snapshot(graph, path):
    graph.temporal.commit_versions([NodeVersionWrite.assertion(Node(f"n{index}"), (0, None)) for index in range(5)])
    for edge, valid in (
        (Edge("e0", "n0", "n1", properties={"type": "R"}), (0, 10)),
        (Edge("e1", "n1", "n2", properties={"type": "R"}), (10, 20)),
        (Edge("e2", "n2", "n3", properties={"type": "S"}), (20, None)),
        (Edge("e3", "n3", "n4", properties={"type": "R"}), (0, 20)),
    ):
        graph.temporal.put_edge_version(edge, valid=valid)
    return graph.build_sampler_snapshot(path, temporal=True, time_bucket="none")


def induced_edges(snapshot, nodes, relation_ids=None):
    selected = set(int(node) for node in nodes)
    return {
        edge
        for edge in range(snapshot.num_edges)
        if int(snapshot.src_int[edge]) in selected
        and int(snapshot.dst_int[edge]) in selected
        and (
            relation_ids is None
            or int(snapshot.rel_int[edge]) in relation_ids
        )
    }


def test_induced_subgraph_includes_only_eligible_edges(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=11)
    batch = engine.sample_induced_subgraph(3, max_edges=10)

    nodes = batch.node_ids_global.tolist()
    assert batch.n_nodes == 3
    assert len(set(nodes)) == 3
    assert set(batch.edge_ids_global.tolist()) == induced_edges(snapshot, nodes)
    assert batch.positives.size == 0
    assert batch.negatives.size == 0
    for local_src, local_dst, edge in zip(
        batch.senders, batch.receivers, batch.edge_ids_global
    ):
        assert batch.node_ids_global[local_src] == snapshot.src_int[edge]
        assert batch.node_ids_global[local_dst] == snapshot.dst_int[edge]


def test_induced_subgraph_is_deterministic_for_seed(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    first = SamplerEngine(snapshot, seed=13).sample_induced_subgraph(4, max_edges=20)
    second = SamplerEngine(snapshot, seed=13).sample_induced_subgraph(4, max_edges=20)

    assert first.node_ids_global.tolist() == second.node_ids_global.tolist()
    assert first.edge_ids_global.tolist() == second.edge_ids_global.tolist()


def test_induced_subgraph_relation_filter(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    relation = snapshot.external_relation_ids.tolist().index("S")
    batch = SamplerEngine(snapshot, seed=17).sample_induced_subgraph(
        snapshot.num_nodes, max_edges=20, relations=[relation]
    )

    assert set(batch.edge_relation_ids.tolist()) == {relation}
    assert set(batch.edge_ids_global.tolist()) == induced_edges(
        snapshot, batch.node_ids_global.tolist(), {relation}
    )


def test_induced_subgraph_fails_closed_on_caps_and_invalid_counts(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=19)

    empty = engine.sample_induced_subgraph(0, max_edges=0)
    assert empty.n_nodes == 0
    assert empty.n_edges == 0

    with pytest.raises(ValueError, match="max_edges=0"):
        engine.sample_induced_subgraph(snapshot.num_nodes, max_edges=0)
    with pytest.raises(ValueError, match="exceeds snapshot"):
        engine.sample_induced_subgraph(snapshot.num_nodes + 1, max_edges=10)
    with pytest.raises(ValueError, match="num_nodes"):
        engine.sample_induced_subgraph(-1, max_edges=10)
    with pytest.raises(ValueError, match="max_edges"):
        engine.sample_induced_subgraph(1, max_edges=-1)
    with pytest.raises(ValueError, match="format-v2 temporal"):
        engine.sample_induced_subgraph(
            1, max_edges=10, temporal=TemporalContext.as_of(0)
        )
    with pytest.raises(ValueError, match="window_policy"):
        engine.sample_induced_subgraph(1, max_edges=10, window_policy="touches")


def test_induced_subgraph_temporal_filter(graph, tmp_path):
    snapshot = build_temporal_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=23)

    point_5 = engine.sample_induced_subgraph(
        snapshot.num_nodes, max_edges=10, temporal=TemporalContext.as_of(5)
    )
    assert set(
        snapshot.external_edge_ids[point_5.edge_ids_global].tolist()
    ) == {"e0", "e3"}

    point_15 = engine.sample_induced_subgraph(
        snapshot.num_nodes, max_edges=10, temporal=TemporalContext.as_of(15)
    )
    assert set(
        snapshot.external_edge_ids[point_15.edge_ids_global].tolist()
    ) == {"e1", "e3"}

    point_25 = engine.sample_induced_subgraph(
        snapshot.num_nodes, max_edges=10, temporal=TemporalContext.as_of(25)
    )
    assert set(
        snapshot.external_edge_ids[point_25.edge_ids_global].tolist()
    ) == {"e2"}

    overlap = engine.sample_induced_subgraph(
        snapshot.num_nodes, max_edges=10, temporal=TemporalContext.during(5, 15)
    )
    assert set(
        snapshot.external_edge_ids[overlap.edge_ids_global].tolist()
    ) == {"e0", "e1", "e3"}

    contained = engine.sample_induced_subgraph(
        snapshot.num_nodes,
        max_edges=10,
        temporal=TemporalContext.during(5, 15),
        window_policy="contained",
    )
    assert contained.n_edges == 0


def test_induced_subgraph_weights_absent_by_default(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    batch = SamplerEngine(snapshot, seed=31).sample_induced_subgraph(3, max_edges=10)

    assert batch.node_weights is None
    assert batch.edge_weights is None
    assert "node_weights" not in batch.to_numpy()
    assert "edge_weights" not in batch.to_numpy()


def test_induced_subgraph_normalize_full_graph(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    batch = SamplerEngine(snapshot, seed=37).sample_induced_subgraph(
        snapshot.num_nodes, max_edges=20, normalize=True
    )

    assert batch.node_weights.tolist() == [1.0] * snapshot.num_nodes
    assert batch.edge_weights.tolist() == [1.0] * batch.n_edges
    assert batch.to_numpy()["node_weights"].tolist() == batch.node_weights.tolist()


def test_induced_subgraph_normalize_partial_graph(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    batch = SamplerEngine(snapshot, seed=41).sample_induced_subgraph(
        3, max_edges=10, normalize=True
    )
    total = snapshot.num_nodes

    assert batch.node_weights.tolist() == [total / 3] * batch.n_nodes
    pair_weight = total * (total - 1) / (3 * 2)
    assert batch.edge_weights.tolist() == [pair_weight] * batch.n_edges


def test_induced_subgraph_normalize_self_loops(tmp_path):
    from gestaltdb.sampling import SamplerSnapshot

    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=["n0", "n1", "n2"],
        external_edge_ids=["loop0", "loop1", "loop2"],
        external_relation_ids=["R"],
        src_int=[0, 1, 2],
        dst_int=[0, 1, 2],
        rel_int=[0, 0, 0],
    )
    batch = SamplerEngine(snapshot, seed=43).sample_induced_subgraph(
        1, max_edges=10, normalize=True
    )

    assert batch.n_nodes == 1
    assert batch.n_edges == 1
    assert batch.node_weights.tolist() == [3.0]
    assert batch.edge_weights.tolist() == [3.0]


def test_induced_subgraph_normalize_empty_and_validation(graph, tmp_path):
    snapshot = build_plain_snapshot(graph, tmp_path / "snapshot")
    engine = SamplerEngine(snapshot, seed=47)

    empty = engine.sample_induced_subgraph(0, max_edges=0, normalize=True)
    assert empty.node_weights.size == 0
    assert empty.edge_weights.size == 0

    with pytest.raises(ValueError, match="normalize"):
        engine.sample_induced_subgraph(1, max_edges=10, normalize="yes")
