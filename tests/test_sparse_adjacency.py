"""Sparse encoding preserves legacy ordering without allocating N*R offsets."""

import json
import shutil
from dataclasses import fields

import numpy as np
import pytest

from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot
from gestaltdb.sampling.adjacency import build_csr, build_sparse_csr
from .snapshot_helpers import dense_snapshot, reseal


def make_snapshot(path, *, nodes=31, relations=9, edges=70):
    rng = np.random.default_rng(45)
    return SamplerSnapshot.from_edge_arrays(
        path, external_node_ids=[f"n{i}" for i in range(nodes)],
        external_edge_ids=[f"e{i}" for i in range(edges)],
        external_relation_ids=[f"r{i}" for i in range(relations)],
        src_int=rng.integers(0, max(1, nodes - 1), edges),
        dst_int=rng.integers(0, max(1, nodes - 1), edges),
        rel_int=rng.integers(0, max(1, relations - 1), edges),
        edge_weights=rng.random(edges),
    )


def assert_batches_equal(left, right):
    for field in fields(left):
        a, b = getattr(left, field.name), getattr(right, field.name)
        if isinstance(a, np.ndarray):
            np.testing.assert_array_equal(a, b)
        else:
            assert a == b


def test_sparse_builder_order_and_missing_rows():
    keys = np.array([99, 3, 99, 3, 99], dtype=np.int64)
    edges = np.arange(5, dtype=np.int64)
    sparse = build_sparse_csr(keys, edges, 10**12)
    assert sparse.row_keys.tolist() == [3, 99]
    assert sparse.indptr.tolist() == [0, 2, 5]
    assert sparse.edge_range(99).tolist() == [0, 2, 4]
    for key in (-1, 0, 4, 100, 10**12):
        assert sparse.edge_range(key).size == 0
    starts = np.array([7, 3, 2, 3, 2], dtype=np.int64)
    temporal = build_sparse_csr(keys, edges, 10**12, starts=starts)
    assert temporal.edge_range(99).tolist() == [2, 4, 0]
    with pytest.raises(ValueError, match="int64"):
        build_sparse_csr(keys, edges, 2**63)


@pytest.mark.parametrize("mmap", [False, True])
@pytest.mark.parametrize("nodes,relations", [(0, 0), (5, 0), (5, 7)])
def test_empty_sparse_roundtrip(tmp_path, mmap, nodes, relations):
    snapshot = make_snapshot(tmp_path / "s", nodes=nodes, relations=relations, edges=0)
    loaded = SamplerSnapshot.load(snapshot.path, mmap=mmap)
    for adjacency in (loaded.relation_out, loaded.relation_in):
        assert adjacency.indptr.tolist() == [0]
        assert adjacency.row_keys.size == 0
        assert adjacency.edge_range(0).size == 0
        if mmap:
            assert isinstance(adjacency.row_keys, np.memmap)


@pytest.mark.parametrize("mmap", [False, True])
def test_legacy_v1_v2_load_and_seeded_parity(tmp_path, mmap):
    sparse = make_snapshot(tmp_path / "s")
    shutil.copytree(sparse.path, tmp_path / "dense")
    dense_snapshot(tmp_path / "dense")
    dense = SamplerSnapshot.load(tmp_path / "dense", mmap=mmap)
    sparse = SamplerSnapshot.load(sparse.path, mmap=mmap)
    assert sparse.metadata["relation_adjacency_encoding"] == "sparse-rows-v1"
    assert dense.relation_out.row_keys is None
    for name in ("relation_out", "relation_in"):
        for key in range(sparse.num_nodes * sparse.num_relations):
            np.testing.assert_array_equal(getattr(sparse, name).edge_range(key), getattr(dense, name).edge_range(key))
    for direction in ("out", "in", "any"):
        for strategy in ("uniform", "weighted"):
            for replace in (False, True):
                a, b = SamplerEngine(sparse, seed=91), SamplerEngine(dense, seed=91)
                kwargs = dict(direction=direction, strategy=strategy, replace=replace, relations=[0, 2, 8])
                assert_batches_equal(a.sample_neighbors([0, 1, 2, 30], 3, **kwargs),
                                     b.sample_neighbors([0, 1, 2, 30], 3, **kwargs))
    metadata = dense.metadata.copy()
    metadata["format_version"] = 1
    metadata.pop("artifacts")
    (dense.path / "metadata.json").write_text(json.dumps(metadata))
    legacy = SamplerSnapshot.load(dense.path, mmap=mmap)
    np.testing.assert_array_equal(legacy.relation_out.indptr, dense.relation_out.indptr)


@pytest.mark.parametrize("corruption", ["unsorted", "duplicate", "negative", "out_of_range", "empty_row", "edge", "shape", "dtype", "missing", "unknown_encoding"])
def test_resealed_malformed_sparse_directory_rejected(tmp_path, corruption):
    snapshot = make_snapshot(tmp_path / "s")
    file = snapshot.path / "relation_out_row_keys.npy"
    rows = np.load(file)
    if corruption in ("unsorted", "duplicate", "negative", "out_of_range", "shape", "dtype"):
        if corruption == "unsorted":
            rows = rows[::-1]
        elif corruption == "duplicate":
            rows[1] = rows[0]
        elif corruption == "negative":
            rows[0] = -1
        elif corruption == "out_of_range":
            rows[-1] = snapshot.num_nodes * snapshot.num_relations
        elif corruption == "shape":
            rows = rows.reshape(1, -1)
        else:
            rows = rows.astype(np.float64)
        np.save(file, rows)
    elif corruption == "empty_row":
        file = snapshot.path / "relation_out_indptr.npy"
        offsets = np.load(file)
        offsets[1] = 0
        np.save(file, offsets)
    elif corruption == "edge":
        file = snapshot.path / "relation_out_edge_indices.npy"
        edges = np.load(file)
        edges[0] = edges[-1]
        np.save(file, edges)
    metadata = snapshot.metadata.copy()
    if corruption == "missing":
        metadata["artifacts"].pop("relation_out_row_keys")
    elif corruption == "unknown_encoding":
        metadata["relation_adjacency_encoding"] = "future-v99"
    reseal(snapshot.path, metadata)
    with pytest.raises(ValueError):
        SamplerSnapshot.load(snapshot.path)


def test_sparse_directory_bytes_scale_with_populated_pairs(tmp_path):
    snapshot = make_snapshot(tmp_path / "s", nodes=2000, relations=128, edges=500)
    adjacency = snapshot.relation_out
    assert adjacency.indptr.size <= snapshot.num_edges + 1
    assert adjacency.row_keys.size <= snapshot.num_edges
    assert adjacency.indptr.nbytes + adjacency.row_keys.nbytes < 0.01 * (2000 * 128 + 1) * 8


def test_row_keys_are_checksum_protected(tmp_path):
    snapshot = make_snapshot(tmp_path / "s")
    file = snapshot.path / "relation_out_row_keys.npy"
    np.save(file, np.load(file)[::-1])
    with pytest.raises(ValueError, match="checksum mismatch: relation_out_row_keys"):
        SamplerSnapshot.load(snapshot.path, mmap=True)


def test_sparse_builder_matches_dense_randomized():
    rng = np.random.default_rng(66)
    for _ in range(30):
        keys = rng.integers(0, 1000, 200, dtype=np.int64)
        edges = np.arange(len(keys), dtype=np.int64)
        sparse, dense = build_sparse_csr(keys, edges, 1000), build_csr(keys, edges, 1000)
        for key in rng.integers(0, 1000, 100):
            np.testing.assert_array_equal(sparse.edge_range(int(key)), dense.edge_range(int(key)))


@pytest.mark.parametrize("operation", ["compact", "weights"])
def test_legacy_dense_rewrite_publishes_sparse_encoding(tmp_path, operation):
    snapshot = make_snapshot(tmp_path / "s")
    dense_snapshot(snapshot.path)
    legacy = SamplerSnapshot.load(snapshot.path)
    assert legacy.relation_out.row_keys is None
    before = legacy.relation_out.edge_range(0).copy()
    if operation == "compact":
        legacy.compact()
    else:
        legacy.set_edge_weights(legacy.edge_weights.copy(), source="test")
    assert legacy.metadata["relation_adjacency_encoding"] == "sparse-rows-v1"
    assert legacy.relation_out.row_keys is not None
    np.testing.assert_array_equal(legacy.relation_out.edge_range(0), before)


def test_multihop_walk_and_negative_batch_seeded_parity(tmp_path):
    sparse = make_snapshot(tmp_path / "s")
    shutil.copytree(sparse.path, tmp_path / "dense")
    dense_snapshot(tmp_path / "dense")
    dense = SamplerSnapshot.load(tmp_path / "dense")
    for sample in (
        lambda engine: engine.sample_random_walks([0, 1, 2], 5, relations=[0, 2], direction="out"),
        lambda engine: engine.sample_multihop([0, 1, 2], [3, 2], relations=[0, 2], direction="in"),
        lambda engine: engine.sample_subgraph([0, 1], [3, 2], relations=[0, 2],
                                             negative_config=HardNegativeConfig()),
    ):
        assert_batches_equal(sample(SamplerEngine(sparse, seed=72)), sample(SamplerEngine(dense, seed=72)))
