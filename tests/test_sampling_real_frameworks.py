"""Real framework conversions for sampled batches (CPU only).

The lightweight stub tests in ``test_sampling_pyg_blocks.py`` and
``test_sampling.py`` replace ``torch``/``dgl`` with ``SimpleNamespace`` fakes.
Those pin field wiring but cannot prove real tensor dtypes, graph
construction, or table schemas. The tests here require the actual optional
dependencies and assert real framework types and values.

Absence behavior stays covered by the stub file's ``blocked_import`` tests;
these tests skip when a framework is not installed.
"""

import numpy as np
import pytest

from gestaltdb.sampling import (
    NeighborSamplingSpec,
    SampledSubgraphBatch,
    SamplerEngine,
    SamplerSnapshot,
)

pytestmark = pytest.mark.framework


@pytest.fixture
def snapshot(tmp_path):
    return SamplerSnapshot.from_edge_arrays(
        tmp_path / "snapshot",
        external_node_ids=[f"n{index}" for index in range(4)],
        external_edge_ids=[f"e{index}" for index in range(3)],
        external_relation_ids=["R"],
        src_int=[0, 0, 1],
        dst_int=[1, 2, 3],
        rel_int=[0, 0, 0],
    )


def test_pyg_blocks_use_real_torch_tensors(snapshot):
    torch = pytest.importorskip("torch")
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_layers([0], [NeighborSamplingSpec(fanout=2, direction="out")])

    tensors = batch.to_pyg_blocks(snapshot)
    blocks = batch.to_blocks(snapshot)

    assert len(tensors) == len(blocks) == 1
    for key in ("src_nodes", "dst_nodes", "edge_index", "edge_ids", "relation_ids"):
        tensor = tensors[0][key]
        assert isinstance(tensor, torch.Tensor), type(tensor)
        assert tensor.dtype == torch.int64
        assert tensor.device.type == "cpu"
        np.testing.assert_array_equal(tensor.numpy(), np.asarray(blocks[0][key]))
    # No torch_geometric import is required for the block dicts.
    assert "torch_geometric" not in tensors[0]
    assert tensors[0]["edge_version_ids"] is None


def test_pyg_uses_real_torch_tensors_and_layout(snapshot):
    torch = pytest.importorskip("torch")
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_subgraph([0], fanouts=[1])

    converted = batch.to_pyg()

    assert isinstance(converted["edge_index"], torch.Tensor)
    assert converted["edge_index"].dtype == torch.int64
    assert converted["edge_index"].shape[0] == 2
    assert converted["edge_index"].shape[1] == batch.n_edges
    for key in ("node_type", "edge_type", "node_id_global", "edge_id_global"):
        assert isinstance(converted[key], torch.Tensor)
        assert converted[key].dtype == torch.int64
    np.testing.assert_array_equal(
        converted["edge_index"].numpy(),
        np.stack([np.asarray(batch.senders), np.asarray(batch.receivers)]),
    )


def test_pyg_preserves_temporal_arrays_with_real_torch():
    torch = pytest.importorskip("torch")
    batch = SampledSubgraphBatch(
        node_ids_global=np.array([0, 1], dtype=np.int64),
        node_type_ids=np.array([-1, -1], dtype=np.int64),
        senders=np.array([0], dtype=np.int64),
        receivers=np.array([1], dtype=np.int64),
        edge_ids_global=np.array([3], dtype=np.int64),
        edge_relation_ids=np.array([0], dtype=np.int64),
        positives=np.empty((0, 3), dtype=np.int64),
        negatives=np.empty((0, 3), dtype=np.int64),
        edge_version_ids=np.array(["00000000-0000-0000-0000-000000000003"]),
        valid_from_us=np.array([10], dtype=np.int64),
        valid_to_us=np.array([20], dtype=np.int64),
        valid_to_open=np.array([False]),
    )

    converted = batch.to_pyg()

    assert converted["edge_version_ids"].tolist() == ["00000000-0000-0000-0000-000000000003"]
    assert isinstance(converted["valid_from_us"], torch.Tensor)
    assert converted["valid_from_us"].dtype == torch.int64
    assert isinstance(converted["valid_to_open"], torch.Tensor)
    assert converted["valid_to_open"].dtype == torch.bool
    np.testing.assert_array_equal(converted["valid_from_us"].numpy(), [10])


def test_pyg_blocks_empty_layer_with_real_torch(snapshot):
    torch = pytest.importorskip("torch")
    engine = SamplerEngine(snapshot, seed=7)
    batch = engine.sample_layers([3], [NeighborSamplingSpec(fanout=2, direction="out")])

    tensors = batch.to_pyg_blocks(snapshot)

    assert tensors[0]["edge_index"].shape == (2, 0)
    assert tensors[0]["edge_index"].dtype == torch.int64
    assert tensors[0]["dst_nodes"].tolist() == [3]


def test_dgl_uses_real_graph_and_torch_tensors(snapshot):
    pytest.importorskip("dgl")
    torch = pytest.importorskip("torch")
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_subgraph([0], fanouts=[1])

    graph, labels = batch.to_dgl()

    assert graph.num_nodes() == batch.n_nodes
    assert graph.num_edges() == batch.n_edges
    for store, key in ((graph.ndata, "type_id"), (graph.edata, "relation_id")):
        assert isinstance(store[key], torch.Tensor)
        assert store[key].dtype == torch.int64
        assert store[key].device.type == "cpu"
    np.testing.assert_array_equal(
        np.stack([graph.edges()[0].numpy(), graph.edges()[1].numpy()]),
        np.stack([np.asarray(batch.senders), np.asarray(batch.receivers)]),
    )
    assert isinstance(labels["positives"], torch.Tensor)


def test_tf_gnns_uses_real_tensors(snapshot):
    tf = pytest.importorskip("tensorflow")
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_subgraph([0], fanouts=[1])

    graph, labels = batch.to_tf_gnns()

    for key in ("nodes", "senders", "receivers", "n_nodes", "n_edges"):
        assert isinstance(graph[key], tf.Tensor), (key, type(graph[key]))
        assert graph[key].dtype == tf.int64
    np.testing.assert_array_equal(graph["senders"].numpy(), np.asarray(batch.senders))
    assert isinstance(labels["positives"], tf.Tensor)


def test_arrow_uses_real_tables(snapshot):
    pa = pytest.importorskip("pyarrow")
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_subgraph([0], fanouts=[1])

    converted = batch.to_arrow()

    assert isinstance(converted["edges"], pa.Table)
    assert isinstance(converted["nodes"], pa.Table)
    assert converted["edges"].schema.names == [
        "edge_id_global", "sender", "receiver", "relation_id"]
    assert converted["edges"].num_rows == batch.n_edges
    assert converted["edges"].column("sender").to_pylist() == list(
        np.asarray(batch.senders).tolist())
