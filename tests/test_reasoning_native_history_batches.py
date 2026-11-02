"""Opt-in history batches retain complete source admission and physical budgets."""

import pytest

from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.native import RawBatch
from gestaltdb.reasoning.adapters.native_locator_claims import NativeLocatorLimits, PinnedNativeLocatorClaimSource
from gestaltdb.reasoning.extraction import ExtractionRequest
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError
from tests.test_reasoning_native_claims import claim, evidence, graph, source
from tests.test_reasoning_native_locator_claims import index_entry, read_view


def batched(view, **kwargs):
    return source(view, PinnedNativeLocatorClaimSource, history_batch_size=3, **kwargs)


@pytest.mark.parametrize("history_batch_size", [1, 2, 64])
@pytest.mark.parametrize("batch_rows", [1, 3])
def test_parity_filters_history_boundaries_and_physical_rows(graph, history_batch_size, batch_rows):
    first = claim(graph)
    claim(graph, "λ\x1f", predicate="q")
    claim(graph, "other-world", world="other")
    claim(graph, "negative", polarity="negative")
    corrected = graph.epistemic.correct_claim(first.logical_id, confidence=.8,
        supersedes_version_id=first.version_id, valid=(20, 80))
    graph.epistemic.retract_claim(first.logical_id, supersedes_version_id=corrected.version_id, valid=(40, 60))
    for time in (19, 20, 40, 60, 80, 100):
        with read_view(graph, valid_time=time) as view:
            for request in (ExtractionRequest("default"), ExtractionRequest("default", predicates=["p", "q"]),
                            ExtractionRequest("default", subject="a"), ExtractionRequest("default", predicates=[])):
                with source(view, PinnedNativeLocatorClaimSource, request=request,
                        history_batch_size=history_batch_size, native_limits=NativeLocatorLimits(batch_rows=batch_rows)) as native:
                    with source(view, PinnedNativeLocatorClaimSource, request=request) as scalar:
                        assert evidence(native) == evidence(scalar)
                        assert native.context == scalar.context
                        assert native.native_profile.raw_index_rows == scalar.native_profile.raw_index_rows
                        assert native.native_profile.materialized_records == scalar.native_profile.materialized_records
                        assert native.native_profile.point_rows == native.native_profile.materialized_records
                    with source(view, PinnedBatchedClaimSource, request=request) as ordinary:
                        assert evidence(native) == evidence(ordinary)


def test_scan_reduction_and_point_accounting_across_chunks(graph):
    for i in range(7):
        claim(graph, str(i))
    with read_view(graph, valid_time=50) as view, batched(view,
            native_limits=NativeLocatorLimits(batch_rows=2)) as native:
        assert len(evidence(native)) == 7
        profile = native.native_profile
        assert profile.scans == 4  # one world scan + ceil(7 / 3) history unions
        assert profile.raw_index_rows == 14
        assert profile.point_rows == profile.materialized_records == 7
        assert profile.point_fetches == 5  # 2 + 2 + 1 independently bounded point chunks
        assert profile.point_bytes > 0


def test_empty_history_and_missing_candidate_keep_zero_point_work(graph):
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        assert evidence(native) == ()
        assert native.native_profile.scans == native.native_profile.point_fetches == 0
    claim(graph)
    with read_view(graph, valid_time=50) as view, batched(view,
            request=ExtractionRequest("default", subject="absent")) as native:
        assert evidence(native) == ()
        assert native.native_profile.point_fetches == native.native_profile.point_rows == 0


def test_batched_snapshot_bytes_do_not_bypass_live_canonical_tamper_checks(graph, monkeypatch):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    from gestaltdb.versioning import TemporalCorruptionError

    first = claim(graph)
    key = graph.temporal.record_key(first.commit_id, first.commit_ordinal)
    original = graph.store.get_metadata(key)
    real = adapter.NativePointScan
    class Tamper:
        def __init__(self, *args, **kwargs):
            self.scan = real(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            # Native point reads still see the old snapshot, but canonical
            # authentication must detect corrupt live records before evidence.
            graph.store.put_metadata(key, b"tampered after native snapshot")
            yield from self.scan
    monkeypatch.setattr(adapter, "NativePointScan", Tamper)
    try:
        with read_view(graph, valid_time=50) as view, batched(view) as native:
            with pytest.raises(TemporalCorruptionError):
                next(native.iter_batches())
    finally:
        graph.store.put_metadata(key, original)


def test_old_pinned_prefix_and_missing_retraction_still_fail_closed(graph):
    first = claim(graph)
    with read_view(graph, valid_time=50) as old:
        with batched(old) as native:
            before = evidence(native)
        retracted = graph.epistemic.retract_claim(first.logical_id, supersedes_version_id=first.version_id)
        with batched(old) as native:
            assert evidence(native) == before
    entry = index_entry(graph, retracted, "logical")
    graph.store.delete_range_index_entry(*entry)
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        with pytest.raises(ProvenanceMismatchError, match="omitted authenticated"):
            evidence(native)


@pytest.mark.parametrize("field", ["max_point_rows", "max_point_bytes", "max_raw_rows", "max_authenticated_records"])
def test_independent_cumulative_limits_and_retry(graph, field):
    for i in range(7):
        claim(graph, str(i))
    with read_view(graph, valid_time=50) as view, batched(view,
            native_limits=NativeLocatorLimits(batch_rows=1, **{field: 3})) as native:
        with pytest.raises(ReasoningLimitError, match="max_rows|max_raw_rows" if field == "max_raw_rows" else field):
            evidence(native)
        assert not native._active
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        assert len(evidence(native)) == 7


@pytest.mark.parametrize("kind", ["range", "point"])
@pytest.mark.parametrize("fault", ["omit", "duplicate", "reorder", "payload"])
def test_forged_batched_streams_fail_before_evidence_and_release_snapshot(graph, monkeypatch, kind, fault):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    for i in range(3):
        claim(graph, str(i))
    name = "NativeRangeScan" if kind == "range" else "NativePointScan"
    real = getattr(adapter, name)
    snapshots = []
    class Faulty:
        def __init__(self, *args, **kwargs):
            snapshots.append(kwargs["snapshot"])
            self.scan = real(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            rows = [row for batch in self.scan for row in batch.rows]
            if fault == "omit":
                rows = rows[:-1]
            elif fault == "duplicate":
                rows.append(rows[-1])
            elif fault == "reorder":
                rows.reverse()
            else:
                rows[0] = (rows[0][0], rows[-1][1])
            for i, row in enumerate(rows):
                yield RawBatch((row,), i, i == len(rows) - 1)
    monkeypatch.setattr(adapter, name, Faulty)
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        with pytest.raises(ProvenanceMismatchError):
            next(native.iter_batches())
        assert snapshots and all(not snapshot.is_valid() for snapshot in snapshots)
        assert not native._active and "_native_versions" not in native.__dict__
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile


def test_cancel_during_points_releases_both_streams_and_allows_retry(graph, monkeypatch):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    for i in range(4):
        claim(graph, str(i))
    ready, scans = False, []
    real = adapter.NativePointScan
    class Observe:
        def __init__(self, *args, **kwargs):
            self.scan = real(*args, **kwargs)
            scans.append(self.scan)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            nonlocal ready
            for batch in self.scan:
                ready = True
                yield batch
    monkeypatch.setattr(adapter, "NativePointScan", Observe)
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        with pytest.raises(ReasoningCancelledError):
            evidence(native, cancel=lambda: ready)
        assert scans and all(not scan._snapshot.is_valid() and scan._closed for scan in scans)
        assert len(evidence(native)) == 4
        iterator = native.iter_batches()
        next(iterator)
        iterator.close()
        assert not native._active and "_native_versions" not in native.__dict__


@pytest.mark.parametrize("fault", ["omit", "duplicate", "old"])
def test_full_grouped_canonical_conformance_survives_batching(graph, monkeypatch, fault):
    from gestaltdb.readview import GraphReadView
    first = claim(graph)
    graph.epistemic.correct_claim(first.logical_id, confidence=.7, supersedes_version_id=first.version_id)
    real = GraphReadView.iter_claims_as_of
    def broken(view, **kwargs):
        rows = list(real(view, **kwargs))
        yield from {"omit": [], "duplicate": rows + rows, "old": [first]}[fault]
    monkeypatch.setattr(GraphReadView, "iter_claims_as_of", broken)
    with read_view(graph, valid_time=50) as view, batched(view) as native:
        with pytest.raises(ProvenanceMismatchError, match="canonical"):
            evidence(native)


def test_explicit_unsupported_batching_never_falls_back(graph, monkeypatch):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    claim(graph)
    def unsupported(store):
        raise ReasoningError("batch capability unavailable")
    monkeypatch.setattr(adapter, "require_native_batching", unsupported)
    with read_view(graph, valid_time=50) as view:
        with pytest.raises(ReasoningError, match="capability unavailable"):
            batched(view)
        with source(view, PinnedNativeLocatorClaimSource) as native:
            assert len(evidence(native)) == 1
        for invalid in (0, -1, True, 65537, "3"):
            with pytest.raises(ReasoningError, match="history_batch_size"):
                source(view, PinnedNativeLocatorClaimSource, history_batch_size=invalid)
