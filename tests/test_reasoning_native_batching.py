"""Actual PyRex range-union and point-fetch framing/snapshot contracts."""

import pytest

from gestaltdb.reasoning.adapters.native_batching import NativePointScan, NativeRangeScan
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError


@pytest.fixture
def db(tmp_path):
    pytest.importorskip("pyarrow")
    pyrex = pytest.importorskip("pyrex")
    handle = pyrex.PyRocksDB(str(tmp_path / "batch"))
    if not all(hasattr(handle, name) for name in ("scan_arrow_ranges", "multi_get_arrow")):
        handle.close()
        pytest.skip("runtime lacks explicit native batching")
    for key in (b"a", b"b", b"c", b"d", b"e", b"\x00\x1f", "λ".encode()):
        handle.put(key, b"value:" + key)
    try:
        yield handle
    finally:
        handle.close()


def rows(scan):
    with scan:
        return [row for batch in scan for row in batch.rows]


@pytest.mark.parametrize("batch_rows", [1, 2, 64])
def test_range_union_order_overlap_dedup_empty_and_binary_keys(db, batch_rows):
    actual = rows(NativeRangeScan(db, ranges=[(b"b", b"e"), (b"a", b"d"),
        (b"b", b"e"), (b"a", b"a"), (b"\x00", b"\x01"), ("λ".encode(), b"\xff")],
        batch_rows=batch_rows))
    assert [key for key, _ in actual] == [b"\x00\x1f", b"a", b"b", b"c", b"d", "λ".encode()]
    assert rows(NativeRangeScan(db, ranges=[(b"z", b"zz")])) == []


@pytest.mark.parametrize("batch_rows", [1, 2, 64])
def test_points_preserve_input_order_duplicates_and_byte_keys(db, batch_rows):
    keys = [b"d", b"a", b"d", b"\x00\x1f", "λ".encode()]
    assert rows(NativePointScan(db, keys=keys, batch_rows=batch_rows)) == [(k, b"value:" + k) for k in keys]
    with pytest.raises(ReasoningError, match="null"):
        rows(NativePointScan(db, keys=[b"a", b"absent", b"b"], batch_rows=batch_rows))


@pytest.mark.parametrize("kind", ["range", "point"])
def test_snapshot_point_horizon_release_foreign_and_early_close(db, tmp_path, kind):
    def scan(snapshot=None):
        options = {"ranges": [(b"a", b"f")]} if kind == "range" else {"keys": [b"b", b"a"]}
        cls = NativeRangeScan if kind == "range" else NativePointScan
        return cls(db, snapshot=snapshot, batch_rows=1, **options)
    snapshot = db.create_snapshot()
    try:
        before = rows(scan(snapshot))
        db.put(b"a", b"changed")
        db.delete(b"b")
        assert rows(scan(snapshot)) == before
        with scan(snapshot) as active:
            iterator = iter(active)
            next(iterator)
            iterator.close()
        assert snapshot.is_valid()  # Borrowers never release the shared pin.
        with scan() as owned:
            pin = owned._snapshot
        assert not pin.is_valid()
        import pyrex
        foreign = pyrex.PyRocksDB(str(tmp_path / "foreign"))
        pin = foreign.create_snapshot()
        try:
            with pytest.raises(ReasoningError, match="snapshot|database"):
                rows(scan(pin))
        finally:
            pin.release()
            foreign.close()
    finally:
        snapshot.release()
    with pytest.raises(ReasoningError, match="snapshot|valid"):
        rows(scan(snapshot))


@pytest.mark.parametrize("kind", ["range", "point"])
def test_batch_rows_bytes_and_cumulative_limit(db, kind):
    cls = NativeRangeScan if kind == "range" else NativePointScan
    options = {"ranges": [(b"a", b"f")]} if kind == "range" else {"keys": [b"a", b"b", b"c", b"d"]}
    with pytest.raises(ReasoningError, match="exceeds"):
        rows(cls(db, max_batch_bytes=1, **options))
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        rows(cls(db, batch_rows=1, max_rows=2, **options))
    scan = cls(db, batch_rows=1, **options)
    rows(scan)
    with pytest.raises(ReasoningError, match="single-use|closed"):
        list(scan)
    scan.close()


def test_invalid_ranges_keys_and_unsupported_explicit_requests(db):
    for ranges in (None, [], [(b"b", b"a")], [(b"a", "b")], [(b"a",)], [(b"a", b"b")] * 65537):
        with pytest.raises(ReasoningError):
            NativeRangeScan(db, ranges=ranges)
    for keys in (None, [], ["a"], [b"a"] * 65537):
        with pytest.raises(ReasoningError):
            NativePointScan(db, keys=keys)
    with pytest.raises(ReasoningError, match="not prefix"):
        NativeRangeScan(db, ranges=[(b"a", b"b")], prefix=b"a")
    with pytest.raises(ReasoningError, match="not prefix"):
        NativePointScan(db, keys=[b"a"], start=b"a")
    class Legacy:
        scan_arrow = db.scan_arrow
        create_snapshot = db.create_snapshot
    with pytest.raises(ReasoningError, match="requires"):
        NativeRangeScan(Legacy(), ranges=[(b"a", b"b")])
    with pytest.raises(ReasoningError, match="requires"):
        NativePointScan(Legacy(), keys=[b"a"])
