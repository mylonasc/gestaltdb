"""Issue #374: correctness-gap regression tests.

Pins the narrowed dispositions: error-contract siblings, store/serializer
rebind rejection on both pinned claim sources, and range memo/verify
soundness (maximal constant-active segments, re-segmented reuse).
"""

import pytest

from gestaltdb.modal import ModalEvaluationLimitError
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.rules import RuleEvaluationLimitError
from gestaltdb.versioning import TemporalVersionError
from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import (
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.temporal_closure import (
    TemporalFact,
    evaluate_range,
    update_range,
    verify_closure,
)
from gestaltdb.reasoning.typed import (
    GenericProgram,
    GenericRule,
    TypedAtom,
    TypedFact,
)

X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def chain_program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def span(start, end):
    return IntervalSet([Interval(start, end)])


def test_limit_errors_are_value_errors_with_unified_catch():
    rule_err = RuleEvaluationLimitError("bound")
    modal_err = ModalEvaluationLimitError("bound")
    typed_err = ReasoningLimitError("bound")
    # All three are ValueError (fail-closed bounds), but rule/modal are NOT
    # ReasoningError: they are TemporalVersionError siblings by design.
    assert isinstance(rule_err, TemporalVersionError)
    assert isinstance(modal_err, TemporalVersionError)
    assert isinstance(typed_err, ReasoningError)
    assert not isinstance(rule_err, ReasoningError)
    assert not isinstance(modal_err, ReasoningError)
    # Unified catch contract documented on ReasoningError/RuleError/ModalError.
    for err in (rule_err, modal_err, typed_err):
        try:
            raise err
        except (ReasoningError, TemporalVersionError):
            pass
        else:  # pragma: no cover
            pytest.fail("unified catch missed a bound error")


def test_pinned_sources_reject_serializer_rebind():
    lmdb = pytest.importorskip("lmdb")
    del lmdb
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Ontology, compile_ontology
    from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
    from gestaltdb.reasoning.adapters.claims import (
        ClaimMapping,
        ClaimSelection,
        PinnedClaimSource,
    )
    from gestaltdb.reasoning.extraction import ExtractionRequest
    from gestaltdb.serializers import JSONSerializer

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        graph = GraphDB.create(tmp + "/db", backend="lmdb", serializer="json")
        try:
            graph.epistemic.assert_claim(subject="a", predicate="knows", object="b",
                               agent="registry", polarity="positive", valid=(0, 100))
            compiled = compile_ontology(Ontology("o", []))
            with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
                sources = [
                    PinnedClaimSource(view, world="default",
                                      ontology_revision=compiled.revision_id,
                                      selection=ClaimSelection(), mapping=ClaimMapping()),
                    PinnedBatchedClaimSource(view, request=ExtractionRequest(world="default"),
                                             ontology_revision=compiled.revision_id,
                                             selection=ClaimSelection(), mapping=ClaimMapping()),
                ]
                real = graph.serializer
                graph.serializer = JSONSerializer()
                try:
                    for source in sources:
                        with pytest.raises(ProvenanceMismatchError):
                            source.verify_source()
                finally:
                    graph.serializer = real
                    for source in sources:
                        source.close()
        finally:
            graph.close()


def test_range_memo_reuse_is_sound_and_resegmented():
    program = chain_program()
    limits = ReasoningLimits()
    base = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 20))]
    closure = evaluate_range(program, base, limits=limits)
    assert len(closure.segments) == 1
    # Identical base: everything reuses, derivations identical.
    updated = update_range(program, closure, base, limits=limits)
    assert updated.reused_segments == len(closure.segments)
    assert updated.recomputed_segments == 0
    assert {d.fact for d in updated.closure.derivations} == \
        {d.fact for d in closure.derivations}
    # Same active set across a split boundary re-segments to new intervals.
    split = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
             TemporalFact(TypedFact("edge", ["a", "b"]), span(10, 20))]
    resplit = update_range(program, closure, split, limits=limits)
    assert {d.fact for d in resplit.closure.derivations} == \
        {d.fact for d in closure.derivations}
    for derivation in resplit.closure.derivations:
        assert derivation.segment in resplit.closure.segments


def test_verify_single_sample_implies_full_point_agreement():
    program = chain_program()
    limits = ReasoningLimits()
    base = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 4)),
            TemporalFact(TypedFact("edge", ["b", "c"]), span(2, 6))]
    closure = evaluate_range(program, base, limits=limits)
    report = verify_closure(program, closure, limits=limits)
    assert report.points_checked == len(closure.segments)
    # Exhaustive check on this small fixture: every integer point agrees.
    for point in range(0, 6):
        assert set(closure.at(point)) == \
            set(evaluate_range(program, base, limits=limits).at(point))
