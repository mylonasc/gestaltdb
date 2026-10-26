"""Authenticated multi-interval publication is one commit, not staged raw triples."""

from dataclasses import replace
from pathlib import Path
import re

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.publication import GenerationStaleError
from gestaltdb.reasoning.adapters.range_publication import (
    RangePublicationLimits, stage_range_generation, publish_range_generation,
)
from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom

X, Y, Z = Variable("x"), Variable("y"), Variable("z")
PROGRAM = GenericProgram([
    GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
    GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [], TypedAtom("reach", [X, Z])),
])


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    a = graph.epistemic.assert_claim(subject="a", predicate="edge", object="b", agent="one", polarity="positive", valid=(0, 40))
    b = graph.epistemic.assert_claim(subject="b", predicate="edge", object="c", agent="two", polarity="positive", valid=(10, 30))
    graph.epistemic.assert_claim(subject="a", predicate="edge", object="b", agent="three", polarity="positive", valid=(60, 80))
    yield graph, a, b
    graph.close()


def stage(graph, **kwargs):
    program = kwargs.pop("program", PROGRAM)
    return stage_range_generation(graph, program, catalog_for_typed_program(program),
        valid_start_us=0, valid_end_us=100, source_world="default", target_world="inferred",
        publication_key="test", selection=ClaimSelection(), mapping=ClaimMapping(), limits=ReasoningLimits(), **kwargs)


def publish(graph, staged, **kwargs):
    return publish_range_generation(graph, staged, limits=ReasoningLimits(), **kwargs)


def visible(graph, point, horizon=None):
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=point, through_commit=horizon) as view:
        return tuple(view.iter_claims_as_of(world="inferred"))


def test_atomic_interval_commit_gaps_metadata_and_pinned_old_view(source):
    graph, _, _ = source
    staged = stage(graph)
    assert not visible(graph, 10)
    horizon = staged.result.binding.provenance.commit_horizon
    outcome = publish(graph, staged, expected_generation=staged.result.binding.identity)
    assert outcome.asserted == len(staged.desired) and outcome.commit_id == horizon + 1
    commit = graph.temporal.get_temporal_commit(outcome.commit_id)
    assert len(commit.versions) == outcome.asserted
    assert commit.metadata["reasoning_generation"] == staged.result.binding.identity.digest
    assert commit.metadata["source_provenance_token"] == staged.result.binding.identity.source.provenance_token
    assert {v.commit_id for v in visible(graph, 10)} == {outcome.commit_id}
    assert not visible(graph, 10, horizon)
    assert not visible(graph, 50)
    assert {v.claim.object for v in visible(graph, 70)} == {"b"}
    assert any(v.claim.subject == "a" and v.claim.object == "c" for v in visible(graph, 10))
    for version in commit.versions:
        assert version.claim.provenance["reasoning_generation"] == commit.metadata["reasoning_generation"]
        assert version.claim.provenance["publication_freshness"] == "latest"


def test_restage_content_idempotence_after_own_commit(source):
    graph, _, _ = source
    first = stage(graph)
    published = publish(graph, first)
    second = stage(graph)
    assert first.content_id == second.content_id
    assert first.result.binding.identity.digest != second.result.binding.identity.digest
    unchanged = publish(graph, second)
    assert unchanged.commit_id is None and unchanged.unchanged == published.asserted
    assert all(v.claim.provenance["reasoning_generation"] == first.result.binding.identity.digest for v in visible(graph, 10))


def test_source_retraction_removes_disappeared_intervals_in_same_commit(source):
    graph, first, _ = source
    publish(graph, stage(graph))
    graph.epistemic.retract_claim(first.logical_id, valid=(0, 40))
    changed = stage(graph)
    outcome = publish(graph, changed)
    assert outcome.retracted > 0
    assert all(v.claim.subject != "a" for v in visible(graph, 10))
    assert any(v.claim.subject == "a" for v in visible(graph, 70))
    assert len(graph.temporal.get_temporal_commit(outcome.commit_id).versions) == outcome.asserted + outcome.corrected + outcome.retracted


def test_latest_and_explicit_historical_source_policy(source):
    graph, _, _ = source
    staged = stage(graph)
    graph.epistemic.assert_claim(subject="new", predicate="edge", object="node", agent="other", polarity="positive", valid=(0, 100))
    horizon = graph.temporal.system_horizon()
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        publish(graph, staged)
    assert graph.temporal.system_horizon() == horizon and not visible(graph, 10)
    published = publish(graph, staged, freshness="historical")
    assert published.commit_id == horizon + 1
    assert all(v.claim.subject != "new" for v in visible(graph, 10))


def test_target_version_cas_not_bypassed_by_historical_or_matching_pin(source):
    graph, _, _ = source
    publish(graph, stage(graph))
    staged = stage(graph)
    target = visible(graph, 10)[0]
    graph.epistemic.correct_claim(target.logical_id, supersedes_version_id=target.version_id,
                        valid=(target.valid.start, target.valid.end))
    horizon = graph.temporal.system_horizon()
    with pytest.raises(GenerationStaleError, match="target versions moved"):
        publish(graph, staged, freshness="historical", expected_generation=staged.result.binding.identity)
    assert graph.temporal.system_horizon() == horizon


def test_empty_target_aba_does_not_bypass_historical_cas(source):
    graph, _, _ = source
    old_empty = stage(graph)
    outcome = publish(graph, old_empty)
    for version in graph.temporal.get_temporal_commit(outcome.commit_id).versions:
        graph.epistemic.retract_claim(version.logical_id, valid=(version.valid.start, version.valid.end))
    assert not visible(graph, 10)
    horizon = graph.temporal.system_horizon()
    with pytest.raises(GenerationStaleError, match="target versions moved"):
        publish(graph, old_empty, freshness="historical")
    assert graph.temporal.system_horizon() == horizon


def test_partial_external_retraction_fails_closed(source):
    graph, _, _ = source
    publish(graph, stage(graph))
    target = next(v for v in visible(graph, 10) if v.valid.start.epoch_microseconds == 0)
    graph.epistemic.retract_claim(target.logical_id, valid=(10, 20))
    with pytest.raises(ReasoningError, match="partially changed"):
        stage(graph)


def test_altered_outputs_or_source_evidence_never_write(source):
    graph, _, _ = source
    staged = stage(graph)
    horizon = graph.temporal.system_horizon()
    for bad in (replace(staged, desired=staged.desired[1:]), replace(staged, content_id="other"),
                replace(staged, result=replace(staged.result, evidence=staged.result.evidence[1:]))):
        with pytest.raises((ReasoningError, ProvenanceMismatchError)):
            publish(graph, bad)
        assert graph.temporal.system_horizon() == horizon and not visible(graph, 10)


def test_shape_limits_and_cancellation_preserve_database(source):
    graph, _, _ = source
    horizon = graph.temporal.system_horizon()
    unary = GenericProgram([GenericRule("unary", [TypedAtom("edge", [X, Y])], [], TypedAtom("person", [X]))])
    with pytest.raises(ReasoningError, match="binary entity output"):
        stage(graph, program=unary)
    with pytest.raises(ReasoningLimitError, match="max_outputs"):
        stage(graph, publication_limits=RangePublicationLimits(max_outputs=1))
    staged = stage(graph)
    with pytest.raises(ReasoningLimitError, match="max_writes"):
        publish(graph, staged, publication_limits=RangePublicationLimits(max_writes=1))
    with pytest.raises(ReasoningCancelledError):
        publish(graph, staged, cancel=lambda: True)
    assert graph.temporal.system_horizon() == horizon and not visible(graph, 10)


def test_expected_pin_rejects_before_guard_and_source_scan(source, monkeypatch):
    graph, _, _ = source
    staged = stage(graph)
    wrong = replace(staged.result.binding.identity, catalog_digest="wrong")
    def forbidden():
        pytest.fail("entered writer guard before exact pin rejection")
    monkeypatch.setattr(graph.temporal, "write_guard", forbidden)
    with pytest.raises(ReasoningError, match="catalog_digest"):
        publish(graph, staged, expected_generation=wrong)


def test_stale_computation_cannot_be_laundered_through_new_staging(source):
    graph, _, _ = source
    old = stage(graph)
    graph.epistemic.assert_claim(subject="fresh", predicate="edge", object="node", agent="new", polarity="positive", valid=(1, 2))
    new = stage(graph)
    laundered = replace(new, result=old.result, desired=old.desired, content_id=old.content_id)
    horizon = graph.temporal.system_horizon()
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        publish(graph, laundered)
    assert graph.temporal.system_horizon() == horizon and not visible(graph, 1)
    publish(graph, new)
    assert any(v.claim.subject == "fresh" for v in visible(graph, 1))


def test_commit_failure_before_and_after_visibility_is_retry_safe(source, monkeypatch):
    graph, _, _ = source
    staged = stage(graph)
    commit = graph.temporal.commit_versions
    def before(*args, **kwargs):
        raise RuntimeError("before commit")
    monkeypatch.setattr(graph.temporal, "commit_versions", before)
    with pytest.raises(RuntimeError, match="before"):
        publish(graph, staged)
    assert not visible(graph, 10)
    def after(*args, **kwargs):
        commit(*args, **kwargs)
        raise RuntimeError("after visibility")
    monkeypatch.setattr(graph.temporal, "commit_versions", after)
    with pytest.raises(RuntimeError, match="after"):
        publish(graph, staged)
    assert visible(graph, 10)
    monkeypatch.setattr(graph.temporal, "commit_versions", commit)
    assert publish(graph, stage(graph)).commit_id is None


def test_nontransactional_marker_failure_requires_explicit_orphan_recovery(source, monkeypatch):
    from gestaltdb.graphdb import _TEMPORAL_VISIBLE_PREFIX
    from gestaltdb.versioning import TemporalCorruptionError
    graph, _, _ = source
    if graph.store.supports_transactions:
        pytest.skip("nontransactional marker fault injection")
    staged = stage(graph)
    original = graph.store.put_metadata
    def interrupted(key, value):
        if key.startswith(_TEMPORAL_VISIBLE_PREFIX):
            raise RuntimeError("interrupted before marker")
        return original(key, value)
    monkeypatch.setattr(graph.store, "put_metadata", interrupted)
    with pytest.raises(RuntimeError, match="marker"):
        publish(graph, staged)
    monkeypatch.setattr(graph.store, "put_metadata", original)
    with pytest.raises(TemporalCorruptionError):
        visible(graph, 10)
    with pytest.raises(ReasoningError, match="orphan recovery"):
        publish(graph, staged)
    report = graph.temporal.list_temporal_orphans()
    graph.temporal.reclaim_temporal_orphans(through_commit=report.through_commit)
    graph.temporal.rebuild_temporal_indexes()
    assert not visible(graph, 10)
    assert publish(graph, stage(graph)).asserted == len(staged.desired)


def test_writer_guard_covers_readmission_through_commit(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import gestaltdb.reasoning.adapters.range_publication as adapter
    graph, _, _ = source
    staged = stage(graph)
    entered, release, attempted, finished = Event(), Event(), Event(), Event()
    original = adapter.admit_range_result
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    def writer():
        attempted.set()
        graph.epistemic.assert_claim(subject="concurrent", predicate="edge", object="node", agent="new", polarity="positive", valid=(0, 100))
        finished.set()
    monkeypatch.setattr(adapter, "admit_range_result", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        published = pool.submit(publish, graph, staged)
        try:
            assert entered.wait(5)
            written = pool.submit(writer)
            assert attempted.wait(5)
            assert not finished.wait(0.05)
        finally:
            release.set()
        outcome = published.result(timeout=5)
        written.result(timeout=5)
    assert graph.temporal.system_horizon() == outcome.commit_id + 1


def test_target_history_bounds_and_unmanaged_namespaces_fail_before_writes(source):
    graph, _, _ = source
    with pytest.raises(ReasoningLimitError, match="max_target_history"):
        stage(graph, publication_limits=RangePublicationLimits(max_target_history=1))
    staged = stage(graph)
    graph.epistemic.assert_claim(subject="manual", predicate="p", object="value", agent="other",
                       source=staged.namespace + ":fake", world="inferred", polarity="positive", valid=(0, 1))
    horizon = graph.temporal.system_horizon()
    with pytest.raises(ReasoningError, match="unmanaged"):
        stage(graph)
    assert graph.temporal.system_horizon() == horizon


def test_historical_final_horizon_check_rejects_reentrant_movement(source, monkeypatch):
    import gestaltdb.reasoning.adapters.range_publication as adapter
    graph, _, _ = source
    staged = stage(graph)
    original = adapter._target_state
    horizon = graph.temporal.system_horizon()
    def moved(*args, **kwargs):
        state = original(*args, **kwargs)
        graph.epistemic.assert_claim(subject="reentrant", predicate="other", object="value", agent="test", polarity="positive", valid=(0, 1))
        return state
    monkeypatch.setattr(adapter, "_target_state", moved)
    with pytest.raises(GenerationStaleError, match="while preparing"):
        publish(graph, staged, freshness="historical")
    assert graph.temporal.system_horizon() == horizon + 1 and not visible(graph, 10)


def test_cancellation_after_readmission_preserves_target(source, monkeypatch):
    import gestaltdb.reasoning.adapters.range_publication as adapter
    graph, _, _ = source
    staged = stage(graph)
    original = adapter._target_state
    cancelled = []
    def cancel_after(*args, **kwargs):
        state = original(*args, **kwargs)
        cancelled.append(True)
        return state
    monkeypatch.setattr(adapter, "_target_state", cancel_after)
    horizon = graph.temporal.system_horizon()
    with pytest.raises(ReasoningCancelledError):
        publish(graph, staged, cancel=lambda: bool(cancelled))
    assert graph.temporal.system_horizon() == horizon and not visible(graph, 10)


def test_bound_range_publication_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-bound-range-publication.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})
