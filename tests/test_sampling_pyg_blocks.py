"""Sampling roadmap (#106): PyTorch tensor exports for layered blocks."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from gestaltdb.sampling import NeighborSamplingSpec, SamplerEngine, SamplerSnapshot

from .conftest import blocked_import


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


@pytest.fixture
def fake_torch(monkeypatch):
    fake = SimpleNamespace(
        long=np.int64,
        bool=np.bool_,
        as_tensor=lambda value, dtype: np.asarray(value, dtype=dtype),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    return fake


def test_pyg_blocks_mirror_numpy_blocks(snapshot, fake_torch):
    engine = SamplerEngine(snapshot, seed=3)
    batch = engine.sample_layers(
        [0], [NeighborSamplingSpec(fanout=2, direction="out")]
    )
    tensors = batch.to_pyg_blocks(snapshot)
    blocks = batch.to_blocks(snapshot)

    assert len(tensors) == len(blocks) == 1
    for key in ("src_nodes", "dst_nodes", "edge_index", "edge_ids", "relation_ids"):
        assert tensors[0][key].tolist() == blocks[0][key].tolist()
        assert tensors[0][key].dtype == np.int64
    assert tensors[0]["edge_version_ids"] is None
    assert tensors[0]["valid_from_us"] is None
    assert tensors[0]["valid_to_us"] is None
    assert tensors[0]["valid_to_open"] is None


def test_pyg_blocks_require_torch(snapshot):
    engine = SamplerEngine(snapshot, seed=5)
    batch = engine.sample_layers(
        [0], [NeighborSamplingSpec(fanout=2, direction="out")]
    )
    with blocked_import("torch"):
        with pytest.raises(ImportError, match="torch"):
            batch.to_pyg_blocks(snapshot)


def test_pyg_blocks_handle_empty_layers(snapshot, fake_torch):
    engine = SamplerEngine(snapshot, seed=7)
    batch = engine.sample_layers(
        [3], [NeighborSamplingSpec(fanout=2, direction="out")]
    )
    tensors = batch.to_pyg_blocks(snapshot)

    assert tensors[0]["edge_index"].shape == (2, 0)
    assert tensors[0]["dst_nodes"].tolist() == [3]
