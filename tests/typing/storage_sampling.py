"""Static consumer contracts; checked by test_static_typing_contracts.py."""

from collections.abc import Iterator

import numpy as np
from typing_extensions import assert_type

from gestaltdb.kvstores import KVStore, LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.sampling._arrays import BoolArray, FloatArray, IntArray
from gestaltdb.sampling.adjacency import CSRAdjacency
from gestaltdb.sampling.batch import SampledSubgraphBatch
from gestaltdb.sampling.engine import SamplerEngine
from gestaltdb.sampling.feeder import AsyncBatchFeeder
from gestaltdb.sampling.snapshot import SamplerSnapshot


def storage_contracts(store: KVStore, lmdb: LMDBStore, leveldb: LevelDBStore, pyrex: PyRexStore) -> None:
    assert_type(store.get_node(b"n"), bytes | None)
    assert_type(lmdb.get_metadata(b"missing"), bytes | None)
    assert_type(leveldb.get_node(b"missing"), bytes | None)
    assert_type(pyrex.get(b"missing"), bytes | None)
    assert_type(store.get_nodes_bulk([b"missing"]), dict[bytes, bytes])
    assert_type(store.get_node_keys_generator(), Iterator[bytes])
    assert_type(store.iter_typed_adjacency(b"n", "R"), Iterator[tuple[bytes, bytes]])
    assert_type(store.iter_index_prefix("label", [b"L"]), Iterator[bytes])
    assert_type(store.iter_range_index("score", [b"L"], reverse=True, limit=1), Iterator[bytes])
    assert_type(lmdb.transaction().get_node(b"n"), bytes | None)
    assert_type(pyrex.transaction().get_node(b"n"), bytes | None)
    with lmdb.read_snapshot() as snapshot:
        assert_type(snapshot.get_node(b"n"), bytes | None)
        assert_type(snapshot.snapshot_identity.sequence, int | None)
    store.put_node("n", b"payload")  # type: ignore[arg-type]
    store.put_node(b"n", "payload")  # type: ignore[arg-type]
    missing: bytes = pyrex.get_node(b"missing")  # type: ignore[assignment]


def array_contracts(snapshot: SamplerSnapshot, engine: SamplerEngine, batch: SampledSubgraphBatch) -> None:
    assert_type(snapshot.src_int, IntArray)
    assert_type(snapshot.valid_to_open, BoolArray | None)
    assert_type(snapshot.edge_weights, FloatArray)
    assert_type(snapshot.node_ints(["n"]), IntArray)
    assert_type(snapshot.out.edge_range(0), IntArray)
    assert_type(engine.sample_nodes(1), IntArray)
    assert_type(engine.sample_neighbors([0], fanout=1).neighbor_nodes, IntArray)
    arrays = batch.to_numpy()
    assert_type(arrays["senders"], IntArray)
    assert_type(arrays["valid_from_us"], IntArray | None)
    assert_type(arrays["negative_time_us"], IntArray | None)
    assert_type(AsyncBatchFeeder(lambda: 42).get(), int)
    floats: FloatArray = np.asarray([1.0], dtype=np.float64)
    CSRAdjacency(floats, snapshot.src_int)  # type: ignore[arg-type]
    ids: IntArray = floats  # type: ignore[assignment]
    batch.negative_time_us = floats  # type: ignore[assignment]
