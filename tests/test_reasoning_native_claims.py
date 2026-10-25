"""Native bytes admit only complete canonical point-selected evidence."""

from dataclasses import replace
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, ClaimSourceLimits
from gestaltdb.reasoning.adapters.native import RawBatch
from gestaltdb.reasoning.adapters.native_claims import NativeClaimLimits, PinnedNativeClaimSource
from gestaltdb.reasoning.extraction import ExtractionRequest
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    graph = GraphDB.create(tmp_path / "db", backend="pyrex", serializer="json")
    try:
        yield graph
    finally:
        graph.close()


def claim(graph, subject="a", **kwargs):
    values = dict(subject=subject, predicate="p", object="b", agent="registry",
                  polarity="positive", valid=(0, 100))
    values.update(kwargs)
    return graph.assert_claim(**values)


def source(view, cls=PinnedNativeClaimSource, **kwargs):
    values = dict(request=ExtractionRequest(world="default", batch_rows=1),
                  ontology_revision="rev", selection=ClaimSelection(), mapping=ClaimMapping())
    values.update(kwargs)
    return cls(view, **values)


def evidence(src, **kwargs):
    return tuple(e for batch in src.iter_batches(**kwargs) for e in batch.evidence)


def parity(view, **kwargs):
    with source(view, **kwargs) as native, source(view, PinnedBatchedClaimSource, **kwargs) as ordinary:
        actual, expected = evidence(native), evidence(ordinary)
        assert actual == expected
        assert native.context == ordinary.context and native.provenance == ordinary.provenance
        assert native.profile.rows == len(actual)
        assert "post-selection" in native.profile.pushdown
        assert native.native_profile.raw_rows >= native.native_profile.visible_records
        return actual


def test_native_history_corrections_retractions_alternatives_and_half_open_points(graph):
    first = claim(graph)
    alternative = claim(graph, agent="independent")
    claim(graph, "expired", valid=(0, 50))
    claim(graph, "starts", valid=(50, 100))
    claim(graph, "negative", polarity="negative")
    claim(graph, "literal", object_kind="literal", object=42)
    claim(graph, "other-world", world="other")
    with graph.read_view(valid_time=50) as old:
        before = parity(old)
        corrected = graph.correct_claim(first.logical_id, confidence=0.7, valid=(20, 80), supersedes_version_id=first.version_id)
        with graph.read_view(valid_time=50) as corrected_view:
            after = parity(corrected_view)
            assert corrected.version_id in {e.reference_id for e in after}
            assert first.version_id not in {e.reference_id for e in after}
            graph.retract_claim(first.logical_id, valid=(40, 60), supersedes_version_id=corrected.version_id)
            assert parity(corrected_view) == after
        assert parity(old) == before
        with graph.read_view(valid_time=50) as latest:
            latest_rows = parity(latest)
            assert alternative.version_id in {e.reference_id for e in latest_rows}
            assert corrected.version_id not in {e.reference_id for e in latest_rows}
            assert {e.fact.subject for e in latest_rows} == {"a", "starts"}
        for time in (0, 19, 20, 39, 40, 59, 60, 79, 80, 99, 100):
            with graph.read_view(valid_time=time) as view:
                parity(view)


@pytest.mark.parametrize("request_args,selection_args", [
    ({}, {}), ({"predicates": []}, {}), ({"predicates": ["p", "q"]}, {}),
    ({"subject": "a"}, {}), ({"object": "b"}, {}), ({"object": "absent"}, {}),
    ({"agents": []}, {}), ({"agents": ["registry"]}, {"agents": ["independent"]}),
    ({"sources": [None]}, {}), ({}, {"sources": ["trusted"]}),
    ({}, {"include_derived": True}),
])
def test_native_policy_context_mapping_and_post_selection_filters(graph, request_args, selection_args):
    claim(graph)
    claim(graph, "c", agent="independent", source="trusted", predicate="q")
    claim(graph, "derived", agent="gestaltdb:rules", provenance={"derived_by": "gestaltdb.rules"})
    with graph.read_view(valid_time=50) as view:
        parity(view, request=ExtractionRequest(world="default", **request_args),
               selection=ClaimSelection(**selection_args), mapping=ClaimMapping((("p", "logical"), ("q", "logical"))))


def test_empty_history_and_nonclaim_records_are_not_evidence(graph):
    from gestaltdb.graphdb import Node
    with graph.read_view(valid_time=50) as view:
        assert parity(view) == ()
    graph.put_node_version(Node("node"), valid=(0, 100))
    with graph.read_view(valid_time=50) as view, source(view) as native:
        assert evidence(native) == ()
        assert native.native_profile.raw_rows == native.native_profile.visible_records == 1
        assert native.native_profile.logical_claims == 0


@pytest.mark.parametrize("field", NativeClaimLimits.__dataclass_fields__)
@pytest.mark.parametrize("value", [0, True, -1, 1.5])
def test_physical_limit_validation(field, value):
    with pytest.raises(ReasoningError, match=field):
        NativeClaimLimits(**{field: value})


@pytest.mark.parametrize("field", ["max_raw_rows", "max_logical_claims", "max_commits"])
def test_physical_bounds_return_no_success_profile_and_allow_retry(graph, field):
    claim(graph)
    claim(graph, "c")
    with graph.read_view(valid_time=50) as view, source(view, native_limits=NativeClaimLimits(**{field: 1})) as native:
        with pytest.raises(ReasoningLimitError):
            evidence(native)
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.profile
        assert not native._active
    with graph.read_view(valid_time=50) as view:
        assert len(parity(view)) == 2


def test_output_bounds_and_cancellation(graph):
    claim(graph)
    claim(graph, "c")
    with graph.read_view(valid_time=50) as view, source(view) as native:
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 4
        with pytest.raises(ReasoningCancelledError):
            evidence(native, cancel=cancel)
        assert len(evidence(native)) == 2
    with graph.read_view(valid_time=50) as view, source(view, request=ExtractionRequest(world="default", max_rows=1)) as native:
        with pytest.raises(ReasoningLimitError):
            evidence(native)
    with graph.read_view(valid_time=50) as view, source(view, limits=ClaimSourceLimits(max_scanned_claims=1)) as native:
        with pytest.raises(ReasoningLimitError):
            evidence(native)


@pytest.mark.parametrize("fault", ["omit", "duplicate", "wrong-key", "wrong-payload"])
def test_native_scan_stream_cannot_omit_or_forge_authenticated_members(graph, monkeypatch, fault):
    import gestaltdb.reasoning.adapters.native_claims as adapter
    first = claim(graph)
    claim(graph, "c")
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
                rows.pop()
            elif fault == "duplicate":
                rows.append(rows[-1])
            elif fault == "wrong-key":
                rows[0] = (rows[0][0][:-4] + (999).to_bytes(4, "big"), rows[0][1])
            else:
                rows[0] = (rows[0][0], rows[1][1])
            yield RawBatch(tuple(rows), 0, True)
    monkeypatch.setattr(adapter, "NativeKeyScan", FaultyScan)
    with graph.read_view(valid_time=50) as view, source(view) as native:
        with pytest.raises(ProvenanceMismatchError):
            evidence(native)
        with pytest.raises(ReasoningError, match="no completed"):
            _ = native.native_profile


@pytest.mark.parametrize("rebound", ["view", "serializer", "closed"])
def test_rebinding_and_close_invalidate_outstanding_iterators(graph, rebound):
    from gestaltdb.serializers import JSONSerializer
    claim(graph)
    claim(graph, "c")
    claim(graph, "e")
    with graph.read_view(valid_time=50) as view, source(view) as native:
        iterator = native.iter_batches()
        next(iterator)
        original_serializer, original_provenance = graph.serializer, view.provenance
        try:
            if rebound == "view":
                view.provenance = replace(view.provenance, valid_time_us=51)
            elif rebound == "serializer":
                graph.serializer = JSONSerializer()
            else:
                native.close()
            with pytest.raises((ReasoningError, ProvenanceMismatchError)):
                list(iterator)
        finally:
            iterator.close()
            graph.serializer, view.provenance = original_serializer, original_provenance


def test_native_pass_blocks_cooperating_writer_and_abandoned_iterator_releases_guard(graph):
    claim(graph)
    claim(graph, "c")
    claim(graph, "e")
    attempted, finished = Event(), Event()
    def write():
        attempted.set()
        try:
            return claim(graph, "new")
        finally:
            finished.set()
    with graph.read_view(valid_time=50) as view, source(view) as native:
        iterator = native.iter_batches()
        first = next(iterator)
        with pytest.raises(ReasoningError, match="active extraction"):
            next(native.iter_batches())
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(write)
            try:
                assert attempted.wait(5) and not finished.wait(.05)
            finally:
                iterator.close()
            writer.result(timeout=5)
        assert finished.is_set() and not native._active
        assert len(evidence(native)) == 3  # Old pinned prefix, not silently latest.
        assert first.evidence


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel")])
def test_unsupported_backend_does_not_silently_fallback(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend)
    try:
        with graph.read_view(valid_time=50) as view:
            with pytest.raises(ReasoningError, match="plain PyRex"):
                source(view)
    finally:
        graph.close()


def test_transactional_pyrex_capability_rejected_without_fallback(tmp_path):
    pytest.importorskip("pyrex")
    graph = GraphDB.create(tmp_path / "tx", backend="pyrex", backend_options={"transactional": True})
    try:
        with graph.read_view(valid_time=50) as view:
            with pytest.raises(ReasoningError, match="plain PyRex"):
                source(view)
    finally:
        graph.close()


def test_canonical_index_omissions_cannot_hide_native_history(graph, monkeypatch):
    import gestaltdb.readview as views
    claim(graph)
    with graph.read_view(valid_time=50) as view, source(view) as native:
        monkeypatch.setattr(views.GraphReadView, "iter_claims_as_of", lambda *args, **kwargs: iter(()))
        with pytest.raises(ProvenanceMismatchError, match="complete native history"):
            evidence(native)


def test_corruption_after_first_batch_is_not_a_completed_authenticated_pass(graph):
    from gestaltdb.versioning import TemporalCorruptionError
    claim(graph)
    claim(graph, "c")
    last = claim(graph, "e")
    with graph.read_view(valid_time=50) as view, source(view) as native:
        iterator = native.iter_batches()
        next(iterator)
        key = graph._temporal_record_key(last.commit_id, last.commit_ordinal)
        original = graph.store.get_metadata(key)
        try:
            graph.store.put_metadata(key, original + b"damage")
            with pytest.raises((TemporalCorruptionError, ProvenanceMismatchError)):
                list(iterator)
            with pytest.raises(ReasoningError, match="no completed"):
                _ = native.native_profile
        finally:
            iterator.close()
            graph.store.put_metadata(key, original)
        assert len(evidence(native)) == 3


@pytest.mark.parametrize("serializer,module", [("json", None), ("pickle", None),
    ("messagepack", "msgpack"), ("protobuf", "google.protobuf")])
def test_serializers_restart_and_multistep_witness_evidence(tmp_path, serializer, module):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    if module:
        pytest.importorskip(module)
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, compile_ontology
    from gestaltdb.ontology.model import TYPE_PREDICATE
    from gestaltdb.reasoning.model import Fact
    path = tmp_path / serializer
    graph = GraphDB.create(path, backend="pyrex", serializer=serializer)
    first = claim(graph)
    alternate = claim(graph, agent="other")
    compiled = compile_ontology(Ontology("native", [Domain("p", "Child"), SubClassOf("Child", "Parent")]))
    try:
        with graph.read_view(valid_time=50) as view:
            kwargs = dict(ontology_revision=compiled.revision_id)
            with source(view, **kwargs) as native, source(view, PinnedBatchedClaimSource, **kwargs) as ordinary:
                native_result = native.evaluate(compiled, limits=ReasoningLimits(), collect_proofs=True)
                ordinary_result = ordinary.evaluate(compiled, limits=ReasoningLimits(), collect_proofs=True)
                assert native_result == ordinary_result
                assert Fact("a", TYPE_PREDICATE, "Parent") in native_result
                assert {e.reference_id for e in evidence(native)} == {first.version_id, alternate.version_id}
                explanation = native_result.explain(Fact("a", "p", "b"))
                refs = {ref for node in explanation.nodes for ref in node.evidence}
                assert refs and refs <= {first.version_id, alternate.version_id}
                # Existing bounded witness selection is not all-support provenance.
        graph.close()
        graph = GraphDB.open(path)
        with graph.read_view(valid_time=50) as view:
            assert {e.reference_id for e in parity(view)} == {first.version_id, alternate.version_id}
    finally:
        graph.close()


def test_evaluation_forwards_cancellation_into_native_scan(graph, monkeypatch):
    from gestaltdb.ontology import Ontology, compile_ontology
    claim(graph)
    compiled = compile_ontology(Ontology("empty", []))
    callback = lambda: False
    with graph.read_view(valid_time=50) as view, source(view, ontology_revision=compiled.revision_id) as native:
        original = native._read_winners
        calls = []
        def read(cancel):
            calls.append(cancel)
            return original(cancel)
        monkeypatch.setattr(native, "_read_winners", read)
        native.evaluate(compiled, limits=ReasoningLimits(), cancel=callback)
        assert calls == [callback]


def test_extraction_never_writes_or_reads_mutable_graph(graph, monkeypatch):
    claim(graph)
    with graph.read_view(valid_time=50) as view, source(view) as native:
        def forbidden(*args, **kwargs):
            pytest.fail("native extraction attempted writes or mutable graph reads")
        for name in ("put_metadata", "delete_metadata", "put_index_entry", "delete_index_entry",
                     "put_range_index_entry", "delete_range_index_entry"):
            monkeypatch.setattr(graph.store, name, forbidden)
        monkeypatch.setattr(graph, "get_node", forbidden)
        monkeypatch.setattr(graph, "get_edge", forbidden)
        assert len(evidence(native)) == 1


def test_mixed_commit_ordinal_winners_and_interrupted_commit_records(graph, monkeypatch):
    from gestaltdb.epistemic import Claim
    from gestaltdb.graphdb import Node, Edge
    from gestaltdb.versioning import ClaimVersionWrite, NodeVersionWrite, EdgeVersionWrite
    value = Claim(subject="a", predicate="p", object="b", agent="registry", polarity="positive")
    commit = graph.commit_versions([
        NodeVersionWrite.assertion(Node("node"), (0, 100)),
        ClaimVersionWrite.assertion(value, (0, 100)),
        EdgeVersionWrite.assertion(Edge("edge", "a", "b"), (0, 100)),
        ClaimVersionWrite.assertion(value, (0, 100)),
    ])
    with graph.read_view(valid_time=50) as view, source(view) as native:
        rows = evidence(native)
        assert len(rows) == 1 and rows[0].reference_id == commit.versions[-1].version_id
        assert native.native_profile.visible_records == 4
    original_put = graph.store.put_metadata
    def interrupt_marker(key, value):
        if key.startswith(b"temporal:v1:visible:"):
            raise RuntimeError("injected interrupted commit")
        return original_put(key, value)
    with monkeypatch.context() as patch:
        patch.setattr(graph.store, "put_metadata", interrupt_marker)
        with pytest.raises(RuntimeError, match="interrupted"):
            claim(graph, "incomplete")
    claim(graph, "later")
    # An incomplete descriptor blocks the authenticated contiguous prefix.
    with graph.read_view(valid_time=50) as view:
        assert {row.fact.subject for row in parity(view)} == {"a"}
    hidden_key = graph._temporal_record_key(2, 0)
    hidden_value = graph.store.get_metadata(hidden_key)
    graph.reclaim_temporal_orphans(through_commit=2)
    # A leftover raw row in a descriptor-free reservation gap is not evidence.
    graph.store.put_metadata(hidden_key, hidden_value)
    with graph.read_view(valid_time=50) as view, source(view) as native:
        rows = evidence(native)
        assert {row.fact.subject for row in rows} == {"a", "later"}
        assert native.native_profile.raw_rows > native.native_profile.visible_records
        assert rows == parity(view)


def test_native_claim_documentation_example():
    from pathlib import Path
    import re
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    text = (Path(__file__).parents[1] / "docs/reasoning-native-claims.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    for block in blocks:
        exec("\n".join(line[3:] if line.startswith("   ") else line for line in block.splitlines()), {})


@pytest.mark.parametrize("fault", ["duplicate", "old-version", "non-version"])
def test_grouped_canonical_stream_must_exactly_match_native_winners(graph, monkeypatch, fault):
    import gestaltdb.readview as views
    first = claim(graph)
    graph.correct_claim(first.logical_id, confidence=.8, supersedes_version_id=first.version_id)
    original = views.GraphReadView.iter_claims_as_of
    def broken(view, **kwargs):
        rows = list(original(view, **kwargs))
        if fault == "duplicate":
            rows.append(rows[0])
        elif fault == "old-version":
            rows[0] = first
        else:
            rows[0] = "not a version"
        yield from rows
    with graph.read_view(valid_time=50) as view, source(view) as native:
        with monkeypatch.context() as patch:
            patch.setattr(views.GraphReadView, "iter_claims_as_of", broken)
            with pytest.raises(ProvenanceMismatchError):
                evidence(native)
            with pytest.raises(ReasoningError, match="no completed"):
                _ = native.native_profile
        assert len(evidence(native)) == 1


@pytest.mark.parametrize("rows", [100, 200])
def test_native_admission_uses_grouped_integrity_reads_not_per_claim_point_reads(graph, monkeypatch, rows):
    from gestaltdb.epistemic import Claim
    from gestaltdb.versioning import ClaimVersionWrite
    graph.commit_versions([ClaimVersionWrite.assertion(
        Claim(subject=f"entity{i}", predicate="p", object="b", agent="registry", polarity="positive"), (0, 100))
        for i in range(rows)])
    with graph.read_view(valid_time=50) as view:
        reads = 0
        original = graph.store.get_metadata
        def counted(key):
            nonlocal reads
            reads += 1
            return original(key)
        def forbidden(*args, **kwargs):
            pytest.fail("native extraction regressed to per-claim point operations")
        monkeypatch.setattr(graph.store, "get_metadata", counted)
        monkeypatch.setattr(graph, "get_claim_as_of", forbidden)
        with source(view) as native:
            assert len(evidence(native)) == rows
        assert reads <= 80 * rows  # Full group validation remains; no O(rows²) reads.
