"""RE-21 validity-change propagation and managed interval publication."""

import importlib.util

import pytest

from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.temporal_closure import (
    RangeUpdate,
    TemporalFact,
    evaluate_range,
    update_range,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def chain_program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def span(start, end):
    return IntervalSet([Interval(start, end)])


def test_update_reuses_unchanged_segments():
    prog = chain_program()
    base = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
            TemporalFact(TypedFact("edge", ["b", "c"]), span(5, 15))]
    closure = evaluate_range(prog, base, limits=ReasoningLimits())
    assert closure.segments == (Interval(0, 5), Interval(5, 10), Interval(10, 15))
    # Validity shrink on one fact: only affected segments recompute.
    changed = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 7)),
               TemporalFact(TypedFact("edge", ["b", "c"]), span(5, 15))]
    out = update_range(prog, closure, changed, limits=ReasoningLimits())
    assert isinstance(out, RangeUpdate)
    # Pure re-segmentation with identical active sets reuses everything.
    assert (out.reused_segments, out.recomputed_segments) == (3, 0)
    assert out.added == () and out.removed == ()
    assert out.closure.answers(TypedFact("reach", ["a", "c"])) == span(5, 7)
    # A genuinely new active combination forces recomputation.
    extended = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 7)),
                TemporalFact(TypedFact("edge", ["b", "c"]), span(2, 15)),
                TemporalFact(TypedFact("edge", ["c", "d"]), span(3, 4))]
    grown = update_range(prog, out.closure, extended, limits=ReasoningLimits())
    assert grown.recomputed_segments >= 1 and grown.reused_segments >= 1
    assert grown.closure.answers(TypedFact("reach", ["a", "d"])) == span(3, 4)
    assert grown.closure.answers(TypedFact("reach", ["a", "c"])) == span(2, 7)
    fresh = evaluate_range(prog, changed, limits=ReasoningLimits())
    assert set(out.closure.at(point) for point in (3, 6, 9, 12)) == set(
        fresh.at(point) for point in (3, 6, 9, 12))
    assert {item.fact for item in out.closure.derivations} == {item.fact for item in fresh.derivations}
    assert out.closure.answers(TypedFact("reach", ["a", "c"])) == fresh.answers(TypedFact("reach", ["a", "c"]))
    # Non-incremental path agrees exactly (safe full-range fallback).
    rebuilt = update_range(prog, closure, changed, limits=ReasoningLimits(), incremental=False)
    assert rebuilt.recomputed_segments == len(rebuilt.closure.segments) and rebuilt.reused_segments == 0
    assert set(rebuilt.closure.at(point) for point in (3, 6, 9, 12)) == set(
        fresh.at(point) for point in (3, 6, 9, 12))


def test_correction_retraction_and_resurrection():
    prog = chain_program()
    live = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
            TemporalFact(TypedFact("edge", ["b", "c"]), span(0, 10))]
    closure = evaluate_range(prog, live, limits=ReasoningLimits())
    assert closure.answers(TypedFact("reach", ["a", "c"])) == span(0, 10)
    # Retraction: support removed, conclusion follows with exact removal set.
    gone = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10))]
    retracted = update_range(prog, closure, gone, limits=ReasoningLimits())
    assert TypedFact("reach", ["a", "c"]) in retracted.removed
    assert TypedFact("edge", ["b", "c"]) in retracted.removed
    assert retracted.closure.answers(TypedFact("reach", ["a", "c"])).intervals == ()
    # Resurrection: validity regrows, conclusions return with evidence.
    back = update_range(prog, retracted.closure, live, limits=ReasoningLimits())
    assert back.closure.answers(TypedFact("reach", ["a", "c"])) == span(0, 10)
    assert TypedFact("reach", ["a", "c"]) in back.added
    assert back.closure.evidence(TypedFact("reach", ["a", "c"]))
    # Correction (shrink then regrow differently) keeps point parity.
    corrected = [TemporalFact(TypedFact("edge", ["a", "b"]), span(2, 8)),
                 TemporalFact(TypedFact("edge", ["b", "c"]), span(0, 10))]
    out = update_range(prog, back.closure, corrected, limits=ReasoningLimits())
    assert out.closure.answers(TypedFact("reach", ["a", "c"])) == span(2, 8)
    for point in (0, 2, 5, 8, 9):
        active = [item.fact for item in corrected if point in item.valid]
        assert set(out.closure.at(point)) == set(
            GenericEngine().evaluate(prog, active, limits=ReasoningLimits()).facts)


def test_update_validation_and_budgets():
    prog = chain_program()
    base = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 5))]
    closure = evaluate_range(prog, base, limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        update_range(prog, closure, ["nope"], limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        update_range(prog, "nope", base, limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        update_range(prog, closure, base, limits=ReasoningLimits(), incremental="yes")
    with pytest.raises(ReasoningError, match="negation"):
        negated = GenericProgram([GenericRule("n", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                                              [TypedAtom("m", [X])])], closed=["m"])
        update_range(negated, closure, base, limits=ReasoningLimits())
    with pytest.raises(ReasoningCancelledError):
        update_range(prog, closure, base, limits=ReasoningLimits(), cancel=lambda: True)
    crowded = [TemporalFact(TypedFact("edge", [f"n{i}"]), span(i, i + 1)) for i in range(6)]
    with pytest.raises(ReasoningLimitError, match="max_segments"):
        update_range(prog, closure, crowded, limits=ReasoningLimits(), max_segments=2)
    assert update_range(prog, closure, [], limits=ReasoningLimits()).closure.answers(
        TypedFact("reach", ["a", "b"])) == IntervalSet([])


def test_staged_range_validates_without_storage():
    from gestaltdb.reasoning.adapters.publication import (
        GenerationStaleError,
        StagedRangePublication,
        publish_staged_range,
        stage_range,
    )

    with pytest.raises(ReasoningError, match="GraphDB"):
        stage_range(object(), [], valid_start_us=0, valid_end_us=5, evidence_id="e",
                    source_world="b", target_world="d", publication_key="k",
                    selection=object(), mapping=object())
    with pytest.raises(ReasoningError, match="StagedRangePublication"):
        publish_staged_range(object(), "nope")
    staged = StagedRangePublication("ns", "k", "b", "d", 0, 5, "gen", "ev", "ctx",
                                    "rev", "pol", "map", "db", [("a", "p", "b")], "fp")
    assert staged.desired == (("a", "p", "b"),)
    with pytest.raises(ReasoningError, match="max_target_claims"):
        publish_staged_range(object(), staged, max_target_claims=0)
    with pytest.raises(ReasoningError, match="GraphDB"):
        publish_staged_range(object(), staged)
    assert issubclass(GenerationStaleError, ReasoningError)
    with pytest.raises(ReasoningError):
        StagedRangePublication("ns", "k", "b", "d", 5, 5, "gen", "ev", "ctx",
                               "rev", "pol", "map", "db", [], "fp")
    with pytest.raises(ReasoningError):
        StagedRangePublication("ns", "k", "b", "d", 5, 3, "gen", "ev", "ctx",
                               "rev", "pol", "map", "db", [], "fp")


BACKEND = next((module for module in ("lmdb", "plyvel", "pyrex")
                if importlib.util.find_spec(module) is not None), None)


@pytest.mark.skipif(BACKEND is None, reason="no capability-supported storage backend installed")
def test_range_publication_roundtrip_on_backend(tmp_path):
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
    from gestaltdb.reasoning.adapters.publication import (
        audit_publication_orphans,
        publish_staged_range,
        reclaim_publication_orphans,
        stage_range,
    )
    from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
    from gestaltdb.serializers import PickleSerializer

    stores = {"lmdb": LMDBStore, "leveldb": LevelDBStore, "pyrex": PyRexStore}
    graph = GraphDB(stores[BACKEND](path=str(tmp_path / "backend")), PickleSerializer())
    try:
        selection = ClaimSelection(include_derived=False)
        mapping = ClaimMapping()
        assert audit_publication_orphans(graph).artifacts == ()
        with pytest.raises(ReasoningError, match="consent"):
            reclaim_publication_orphans(graph, through_commit=0, consent=False)
        staged = stage_range(graph, [("a", "p", "b")], valid_start_us=1_000_000,
                             valid_end_us=2_000_000, evidence_id="sha256:" + "ab" * 32,
                             source_world="base", target_world="derived", publication_key="rk",
                             selection=selection, mapping=mapping)
        assert staged.namespace.startswith("ontology-range-publication:")
        assert staged.base_fingerprint
        out = publish_staged_range(graph, staged)
        assert (out.asserted, out.corrected, out.retracted, out.unchanged) == (1, 0, 0, 0)
        assert out.commit_id is not None
        republish = publish_staged_range(graph, staged)
        assert (republish.asserted, republish.unchanged) == (0, 1)
        assert republish.commit_id is None
        shrunk = stage_range(graph, [], valid_start_us=1_000_000, valid_end_us=2_000_000,
                             evidence_id="sha256:" + "cd" * 32,
                             source_world="base", target_world="derived", publication_key="rk",
                             selection=selection, mapping=mapping)
        removed = publish_staged_range(graph, shrunk)
        assert removed.retracted == 1 and removed.commit_id is not None
        with pytest.raises(ReasoningError, match="re-stage"):
            publish_staged_range(graph, staged)
    finally:
        graph.close()
