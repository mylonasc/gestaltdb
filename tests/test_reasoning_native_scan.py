"""Native Arrow scan contracts against pyrex-rocksdb>=0.5.0; skipped otherwise."""

import pytest

from gestaltdb.reasoning.adapters.native import (
    MIN_NATIVE_SCAN_VERSION,
    NativeKeyScan,
    RawBatch,
    require_native_scan,
)
from gestaltdb.reasoning.extraction import ExtractionCapability
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError


def _runtime():
    pyarrow = pytest.importorskip("pyarrow")
    pyrex = pytest.importorskip("pyrex")
    from importlib.metadata import version

    if version("pyrex-rocksdb") < MIN_NATIVE_SCAN_VERSION:
        pytest.skip(f"native scans require pyrex-rocksdb>={MIN_NATIVE_SCAN_VERSION}")
    return pyarrow, pyrex


def _database(pyrex, tmp_path, rows=20):
    db = pyrex.PyRocksDB(str(tmp_path / "native"))
    for index in range(rows):
        db.put(f"k:{index:03d}".encode(), f"v{index}".encode())
    db.put(b"", b"empty-key")
    return db


def test_native_entry_points_and_version_gate(tmp_path):
    _, pyrex = _runtime()
    db = _database(pyrex, tmp_path)
    assert require_native_scan(db) is db
    assert ExtractionCapability.probe(db).native_arrow_scan
    with pytest.raises(ReasoningError):
        NativeKeyScan(object())
    with pytest.raises(ReasoningError):
        NativeKeyScan(db, prefix="not-bytes")
    with pytest.raises(ReasoningError):
        NativeKeyScan(db, batch_rows=0)
    with pytest.raises(ReasoningError):
        RawBatch((), 0, False)
    with pytest.raises(ReasoningError):
        RawBatch([("k", "v")], 0, True)


def test_shared_snapshot_horizon_and_range_semantics(tmp_path):
    _, pyrex = _runtime()
    db = _database(pyrex, tmp_path)
    snap = db.create_snapshot()
    first = list(NativeKeyScan(db, snapshot=snap))
    second = list(NativeKeyScan(db, snapshot=snap, prefix=b"k:"))
    assert sum(len(b.rows) for b in first) == 21
    assert sum(len(b.rows) for b in second) == 20
    assert first[-1].complete and all(not b.complete for b in first[:-1])
    assert [b.batch_index for b in first] == list(range(len(first)))
    db.put(b"k:020", b"new")
    assert sum(len(b.rows) for b in NativeKeyScan(db, snapshot=snap)) == 21
    assert sum(len(b.rows) for b in NativeKeyScan(db)) == 22
    ranged = list(NativeKeyScan(db, start=b"k:001", stop=b"k:003"))
    assert sum(len(b.rows) for b in ranged) == 2
    assert ranged[-1].complete
    assert list(NativeKeyScan(db, prefix=b"zzz:")) == []
    with pytest.raises(ReasoningError, match="start"):
        NativeKeyScan(db, start=b"k:003", stop=b"k:001")
    snap.release()


def test_batch_limits_overflow_and_single_use(tmp_path):
    pyarrow, pyrex = _runtime()
    db = _database(pyrex, tmp_path)
    sizes = [len(b.rows) for b in NativeKeyScan(db, batch_rows=7, max_batch_bytes=64)]
    assert sizes and sum(sizes) == 21 and max(sizes) <= 7
    with pytest.raises(ReasoningError, match="exceeds"):
        list(NativeKeyScan(db, max_batch_bytes=1))
    singles = list(NativeKeyScan(db, max_batch_bytes=1, allow_overflow=True))
    assert all(len(b.rows) == 1 for b in singles) and singles[-1].complete
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        list(NativeKeyScan(db, max_rows=5))
    scan = NativeKeyScan(db, prefix=b"k:000")
    assert sum(len(b.rows) for b in scan.iter_batches()) == 1
    with pytest.raises(ReasoningError, match="single-use"):
        list(scan.iter_batches())
    scan.close()
    scan.close()
    raw = db.scan_arrow()
    pyarrow.RecordBatchReader.from_stream(raw)
    with pytest.raises(Exception, match="[Ss]ingle-consumer|already"):
        pyarrow.RecordBatchReader.from_stream(raw)


def test_snapshot_lifetime_foreign_handles_and_copies(tmp_path):
    _, pyrex = _runtime()
    db = _database(pyrex, tmp_path)
    snap = db.create_snapshot()
    batches = list(NativeKeyScan(db, snapshot=snap))
    assert snap.is_valid()
    snap.release()
    assert not snap.is_valid()
    owned = list(NativeKeyScan(db, prefix=b"k:"))
    assert sum(len(b.rows) for b in owned) == 20
    other = pyrex.PyRocksDB(str(tmp_path / "other"))
    foreign = other.create_snapshot()
    try:
        with pytest.raises(ReasoningError, match="[Dd]ifferent database|snapshot"):
            list(NativeKeyScan(db, snapshot=foreign))
    finally:
        foreign.release()
    with NativeKeyScan(db, prefix=b"k:000") as scan:
        first = list(scan.iter_batches())
        assert first[0].rows[0] == (b"k:000", b"v0")
    db.close()
    assert first[0].rows[0] == (b"k:000", b"v0")


def test_gestaltdb_pyrex_store_reports_native_capability(tmp_path):
    _, pyrex = _runtime()
    from gestaltdb.kvstores import PyRexStore

    store = PyRexStore(path=str(tmp_path / "graph"))
    try:
        assert store.has_native_arrow_scan()
        capability = ExtractionCapability.probe(store)
        assert capability.native_arrow_scan
        assert capability.snapshot_kind == "pyrex-native-snapshot"
        assert capability.pinned_snapshot and capability.verifiable_snapshot
        capability.require_pinned_snapshot()
        store.put(b"n\x00a", b"payload")
        rows = [row for batch in NativeKeyScan(store, prefix=b"n\x00") for row in batch.rows]
        assert rows == [(b"n\x00a", b"payload")]
        store.put(b"n\x00b", b"payload")
        second = list(NativeKeyScan(store, prefix=b"n\x00"))
        assert sum(len(b.rows) for b in second) == 2
        assert second[-1].complete
    finally:
        store.close()
