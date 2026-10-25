"""Historical native locator admission, never assertion-fixture shortcuts."""

import pytest

from gestaltdb.graphdb import _TEMPORAL_CLAIM_DIMENSION_INDEX, _TEMPORAL_LOGICAL_VALID_INDEX
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.native import RawBatch
from gestaltdb.reasoning.adapters.native_locator_claims import (
    NativeLocatorLimits, PinnedNativeLocatorClaimSource,
)
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimSelection
from gestaltdb.reasoning.extraction import ExtractionRequest
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError
from gestaltdb.versioning import canonical_json_bytes
from tests.test_reasoning_native_claims import graph, claim, source, evidence


def locator(view, **kwargs):
    return source(view, PinnedNativeLocatorClaimSource, **kwargs)


def parity(view, **kwargs):
    with locator(view, **kwargs) as native, source(view, PinnedBatchedClaimSource, **kwargs) as ordinary:
        actual = evidence(native)
        assert actual == evidence(ordinary)
        assert native.context == ordinary.context
        assert "native-world/predicate/subject" in native.profile.pushdown
        assert native.native_profile.resolved_claims >= len(actual)
        return actual


def test_historical_corrections_retractions_bindings_and_old_prefix(graph):
    first = claim(graph)
    claim(graph, "independent", predicate="q")
    with graph.read_view(valid_time=50) as old:
        before = parity(old)
        corrected = graph.correct_claim(first.logical_id, confidence=.8, valid=(20, 80),
                                         supersedes_version_id=first.version_id)
        graph.retract_claim(first.logical_id, valid=(40, 60), supersedes_version_id=corrected.version_id)
        assert parity(old) == before
        for time in (0, 19, 20, 39, 40, 59, 60, 79, 80, 99, 100):
            with graph.read_view(valid_time=time) as view:
                for request in (
                    ExtractionRequest("default"), ExtractionRequest("default", predicates=["p"]),
                    ExtractionRequest("default", predicates=["p", "q"], subject="a"),
                    ExtractionRequest("default", predicates=["q"], subject="independent", object="b"),
                    ExtractionRequest("default", subject="a"), ExtractionRequest("default", predicates=[]),
                    ExtractionRequest("default", predicates=["absent"]),
                ):
                    parity(view, request=request)


def test_native_prefixes_reduce_transfer_but_report_full_authentication(graph):
    for i in range(10):
        claim(graph, str(i), predicate="p" if i < 3 else "q")
    with graph.read_view(valid_time=50) as view, locator(view,
            request=ExtractionRequest("default", predicates=["p"], subject="0")) as native:
        assert len(evidence(native)) == 1
        profile = native.native_profile
        assert profile.authenticated_records == 10
        assert profile.raw_index_rows == 10 + 3 + 1 + 1  # world, predicate, subject, history
        assert profile.candidate_claims == profile.materialized_records == profile.resolved_claims == 1
        assert profile.scans == 4


@pytest.mark.parametrize("filters", [{}, {"agents": []}, {"sources": [None]}, {"object": "absent"}])
def test_policy_and_literal_negative_world_filter_parity(graph, filters):
    claim(graph)
    claim(graph, "literal", object_kind="literal", object=1)
    claim(graph, "negative", polarity="negative")
    claim(graph, "other", world="other")
    claim(graph, "derived", agent="gestaltdb:rules", provenance={"derived_by": "gestaltdb.rules"})
    with graph.read_view(valid_time=50) as view:
        parity(view, request=ExtractionRequest("default", **filters))
        parity(view, selection=ClaimSelection(include_derived=True))


def index_entry(graph, version, kind):
    generation = graph._temporal_index_generation()
    if kind == "dimension":
        name = graph._temporal_index_name(_TEMPORAL_CLAIM_DIMENSION_INDEX, generation)
        parts = [b"predicate", canonical_json_bytes("p")]
    else:
        name = graph._temporal_index_name(_TEMPORAL_LOGICAL_VALID_INDEX, generation)
        parts = [b"c", version.logical_id.encode()]
    start = graph._temporal_instant_index_value(version.valid.start)
    value = graph._temporal_locator(version.commit_id, version.commit_ordinal)
    return name, parts, start, value


@pytest.mark.parametrize("kind", ["dimension", "logical"])
def test_missing_index_entries_fail_instead_of_silently_omitting_evidence(graph, kind):
    first = claim(graph)
    entry = index_entry(graph, first, kind)
    graph.store.delete_range_index_entry(*entry)
    with graph.read_view(valid_time=50) as view, locator(view,
            request=ExtractionRequest("default", predicates=["p"])) as native:
        with pytest.raises(ProvenanceMismatchError, match="omitted authenticated index"):
            evidence(native)
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile
        graph.store.put_range_index_entry(*entry)
        assert len(evidence(native)) == 1


@pytest.mark.parametrize("fault", ["omit", "duplicate", "payload", "key", "future-start"])
def test_forged_native_index_stream_fails_before_output(graph, monkeypatch, fault):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    claim(graph)
    real = adapter.NativeKeyScan
    class FaultyScan:
        def __init__(self, *args, **kwargs):
            self.scan = real(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            rows = [row for batch in self.scan for row in batch.rows]
            if fault == "omit":
                return
            if fault == "duplicate":
                rows *= 2
            elif fault == "payload":
                rows[0] = (rows[0][0], b"bad")
            elif fault == "key":
                rows[0] = (rows[0][0] + b"forged", rows[0][1])
            else:
                rows[0] = (b"z-outside-prefix", rows[0][1])
            yield RawBatch(tuple(rows), 0, True)
    monkeypatch.setattr(adapter, "NativeKeyScan", FaultyScan)
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        with pytest.raises(ProvenanceMismatchError):
            evidence(native)


@pytest.mark.parametrize("field,value", [("max_authenticated_records", 1), ("max_raw_rows", 1),
    ("max_commits", 1), ("max_logical_claims", 1)])
def test_independent_cumulative_bounds_and_retry(graph, field, value):
    claim(graph)
    claim(graph, "c")
    with graph.read_view(valid_time=50) as view, locator(view,
            native_limits=NativeLocatorLimits(**{field: value})) as native:
        with pytest.raises(ReasoningLimitError, match="max_rows|max_raw_rows" if field == "max_raw_rows" else field):
            evidence(native)
        assert not native._active
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile
    with graph.read_view(valid_time=50) as view:
        assert len(parity(view)) == 2


def test_empty_history_cancel_and_limit_validation(graph):
    with pytest.raises(ReasoningError):
        NativeLocatorLimits(max_authenticated_records=True)
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        assert evidence(native) == () and native.native_profile.scans == 0
        with pytest.raises(ReasoningCancelledError):
            evidence(native, cancel=lambda: True)
        assert evidence(native) == ()


def test_cumulative_row_limit_applies_across_individually_small_scans(graph):
    claim(graph)
    with graph.read_view(valid_time=50) as view, locator(view,
            request=ExtractionRequest("default", predicates=["p"], subject="a"),
            native_limits=NativeLocatorLimits(max_raw_rows=3)) as native:
        # Each prefix has one row; their combined four rows exceed the cap.
        with pytest.raises(ReasoningLimitError, match="max_raw_rows"):
            evidence(native)


def test_cancel_during_native_scan_releases_shared_snapshot_and_allows_retry(graph, monkeypatch):
    import gestaltdb.reasoning.adapters.native_locator_claims as adapter
    claim(graph)
    snapshots = []
    real = adapter.NativeKeyScan
    ready = False
    class ObserveScan:
        def __init__(self, *args, **kwargs):
            self.scan = real(*args, **kwargs)
            snapshots.append(kwargs["snapshot"])
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            nonlocal ready
            for batch in self.scan:
                ready = True
                yield batch
    monkeypatch.setattr(adapter, "NativeKeyScan", ObserveScan)
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        with pytest.raises(ReasoningCancelledError):
            evidence(native, cancel=lambda: ready)
        assert snapshots and all(not snapshot.is_valid() for snapshot in snapshots)
        assert len(evidence(native)) == 1
        assert all(not snapshot.is_valid() for snapshot in snapshots)


@pytest.mark.parametrize("fault", ["omit", "old", "duplicate"])
def test_grouped_canonical_conformance_is_retained(graph, monkeypatch, fault):
    from gestaltdb.readview import GraphReadView
    first = claim(graph)
    graph.correct_claim(first.logical_id, confidence=.8, supersedes_version_id=first.version_id)
    real = GraphReadView.iter_claims_as_of
    def broken(view, **kwargs):
        rows = list(real(view, **kwargs))
        if fault == "omit":
            return
        if fault == "old":
            rows = [first]
        else:
            rows *= 2
        yield from rows
    monkeypatch.setattr(GraphReadView, "iter_claims_as_of", broken)
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        with pytest.raises(ProvenanceMismatchError, match="canonical"):
            evidence(native)


def test_same_commit_ordinals_mixed_kinds_and_writer_iterator_lifetime(graph):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import Node
    from gestaltdb.versioning import ClaimVersionWrite, NodeVersionWrite
    value = Claim(subject="a", predicate="p", object="b", agent="registry", polarity="positive")
    commit = graph.commit_versions([
        NodeVersionWrite.assertion(Node("node"), (0, 100)),
        ClaimVersionWrite.assertion(value, (0, 100)),
        ClaimVersionWrite.assertion(value, (0, 100)),
        ClaimVersionWrite.assertion(Claim(subject="c", predicate="p", object="b", agent="registry", polarity="positive"), (0, 100)),
        ClaimVersionWrite.assertion(Claim(subject="e", predicate="p", object="b", agent="registry", polarity="positive"), (0, 100)),
    ])
    attempted, done = Event(), Event()
    def write():
        attempted.set()
        claim(graph, "new")
        done.set()
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        rows = evidence(native)
        assert {e.reference_id for e in rows} == {v.version_id for v in commit.versions[2:]}
        iterator = native.iter_batches()
        next(iterator)
        with pytest.raises(ReasoningError, match="active extraction"):
            next(native.iter_batches())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            try:
                assert attempted.wait(5) and not done.wait(.05)
            finally:
                iterator.close()
            future.result(timeout=5)
        assert evidence(native) == rows  # Native snapshot cannot upgrade the old view.


@pytest.mark.parametrize("serializer,module", [("json", None), ("pickle", None),
    ("messagepack", "msgpack"), ("protobuf", "google.protobuf")])
def test_serializers_restart_exact_alternative_evidence_and_witnesses(tmp_path, serializer, module):
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Domain, Ontology, compile_ontology
    from gestaltdb.reasoning.model import ReasoningLimits
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    if module:
        pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend="pyrex", serializer=serializer)
    compiled = compile_ontology(Ontology("locator", [Domain("p", "Person")]))
    try:
        first, second = claim(graph), claim(graph, agent="alternative")
        graph.close()
        graph = GraphDB.open(tmp_path / "db")
        with graph.read_view(valid_time=50) as view:
            rows = parity(view)
            assert {e.reference_id for e in rows} == {first.version_id, second.version_id}
            with locator(view, ontology_revision=compiled.revision_id) as native, source(view,
                    PinnedBatchedClaimSource, ontology_revision=compiled.revision_id) as ordinary:
                assert native.evaluate(compiled, limits=ReasoningLimits(), collect_proofs=True) == ordinary.evaluate(
                    compiled, limits=ReasoningLimits(), collect_proofs=True)
    finally:
        graph.close()


def test_missing_retraction_locator_cannot_resurrect_old_assertion(graph):
    first = claim(graph)
    retracted = graph.retract_claim(first.logical_id, supersedes_version_id=first.version_id)
    graph.store.delete_range_index_entry(*index_entry(graph, retracted, "logical"))
    with graph.read_view(valid_time=50) as view, locator(view,
            request=ExtractionRequest("default", predicates=["p"])) as native:
        with pytest.raises(ProvenanceMismatchError, match="omitted authenticated index"):
            evidence(native)


def test_incomplete_descriptor_prefix_and_descriptor_free_gap(graph, monkeypatch):
    claim(graph)
    put = graph.store.put_metadata
    def interrupted(key, value):
        if key.startswith(b"temporal:v1:visible:"):
            raise RuntimeError("injected")
        return put(key, value)
    with monkeypatch.context() as patch:
        patch.setattr(graph.store, "put_metadata", interrupted)
        with pytest.raises(RuntimeError, match="injected"):
            claim(graph, "hidden")
    claim(graph, "later")
    with graph.read_view(valid_time=50) as view:
        assert {e.fact.subject for e in parity(view)} == {"a"}
    graph.reclaim_temporal_orphans(through_commit=2)
    with graph.read_view(valid_time=50) as view:
        assert {e.fact.subject for e in parity(view)} == {"a", "later"}


@pytest.mark.parametrize("rebound", ["view", "serializer", "closed"])
def test_inherited_rebinding_checks_invalidate_partial_locator_pass(graph, rebound):
    from dataclasses import replace
    from gestaltdb.serializers import JSONSerializer
    for subject in ("a", "c", "e"):
        claim(graph, subject)
    with graph.read_view(valid_time=50) as view, locator(view) as native:
        iterator = native.iter_batches()
        next(iterator)
        original_provenance, original_serializer = view.provenance, graph.serializer
        try:
            if rebound == "view":
                view.provenance = replace(view.provenance, valid_time_us=51)
            elif rebound == "serializer":
                graph.serializer = JSONSerializer()
            else:
                native.close()
            with pytest.raises((ProvenanceMismatchError, ReasoningError)):
                list(iterator)
        finally:
            iterator.close()
            view.provenance, graph.serializer = original_provenance, original_serializer


def test_locator_documentation_example():
    from pathlib import Path
    import re
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    text = (Path(__file__).parents[1] / "docs/reasoning-native-locator-claims.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    for block in blocks:
        exec("\n".join(line[3:] if line.startswith("   ") else line for line in block.splitlines()), {})
