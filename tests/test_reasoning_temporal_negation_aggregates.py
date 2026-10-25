"""#393 temporal stratified negation and aggregates over validity intervals."""

import pytest

from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.temporal_closure import (
    TemporalDerivation,
    TemporalFact,
    evaluate_range,
    update_range,
    verify_closure,
)
from gestaltdb.reasoning.typed import (
    AggregateRule,
    Aggregation,
    GenericEngine,
    GenericProgram,
    GenericRule,
    TypedAtom,
    TypedFact,
)


X, Y, N = Variable("x"), Variable("y"), Variable("n")
LIMITS = ReasoningLimits()


def ban_program():
    return GenericProgram([GenericRule(
        "ok", [TypedAtom("person", [X])], [], TypedAtom("ok", [X]),
        negated=[TypedAtom("banned", [X])])], closed=["banned"])


def degree_program():
    return GenericProgram([], aggregates=[AggregateRule(
        "cnt", [TypedAtom("edge", [X, Y])], (X,), (Aggregation("count", Y, N),),
        TypedAtom("deg", [X, N]))])


def base_ban():
    return [TemporalFact(TypedFact("person", ["a"]), IntervalSet([Interval(0, 10)])),
            TemporalFact(TypedFact("banned", ["a"]), IntervalSet([Interval(5, 10)]))]


def test_negation_validity_is_person_minus_ban():
    closure = evaluate_range(ban_program(), base_ban(), limits=LIMITS)
    assert closure.segments == (Interval(0, 5), Interval(5, 10))
    assert len(closure.derivations) == 1
    derivation = closure.derivations[0]
    assert derivation.fact == TypedFact("ok", ["a"])
    assert derivation.segment == Interval(0, 5)
    assert derivation.premises == (TypedFact("person", ["a"]),)
    assert len(derivation.absence) == 1
    probe = derivation.absence[0]
    assert (probe.predicate, probe.args, probe.scope) == ("banned", ("a",), ("banned",))
    assert closure.answers(TypedFact("ok", ["a"])).intervals == (Interval(0, 5),)
    assert TypedFact("ok", ["a"]) in closure.at(2)
    assert TypedFact("ok", ["a"]) not in closure.at(7)
    assert verify_closure(ban_program(), closure, limits=LIMITS).derivations == 1


def test_gaps_stay_gaps_for_negation_and_aggregates():
    closure = evaluate_range(ban_program(), [base_ban()[1]], limits=LIMITS)
    assert closure.derivations == ()
    assert closure.at(7) == (TypedFact("banned", ["a"]),)
    mixed = [TemporalFact(TypedFact("person", ["a"]), IntervalSet([Interval(0, 10)])),
             TemporalFact(TypedFact("edge", ["a", "x"]), IntervalSet([Interval(5, 10)]))]
    agg = evaluate_range(degree_program(), mixed, limits=LIMITS)
    assert agg.segments == (Interval(0, 5), Interval(5, 10))
    assert all(d.segment == Interval(5, 10) for d in agg.derivations)
    assert not [f for f in agg.at(2) if f.predicate == "deg"]


def test_aggregates_count_per_segment_without_zero_inference():
    base = [TemporalFact(TypedFact("edge", ["a", "x"]), IntervalSet([Interval(0, 10)])),
            TemporalFact(TypedFact("edge", ["a", "y"]), IntervalSet([Interval(5, 10)]))]
    closure = evaluate_range(degree_program(), base, limits=LIMITS)
    by_segment = {d.segment: d for d in closure.derivations}
    assert by_segment[Interval(0, 5)].fact == TypedFact("deg", ["a", Literal("integer", 1)])
    assert by_segment[Interval(5, 10)].fact == TypedFact("deg", ["a", Literal("integer", 2)])
    full = by_segment[Interval(5, 10)].aggregates[0]
    assert full.op == "count" and full.group == ("a",) and full.result == Literal("integer", 2)
    assert set(full.operands) == {item.fact for item in base}
    assert verify_closure(degree_program(), closure, limits=LIMITS).derivations == 2


def test_incremental_updates_match_fresh_evaluation():
    closure = evaluate_range(ban_program(), base_ban(), limits=LIMITS)
    extended = [TemporalFact(TypedFact("person", ["a"]), IntervalSet([Interval(0, 10)])),
                TemporalFact(TypedFact("banned", ["a"]), IntervalSet([Interval(3, 10)]))]
    updated = update_range(ban_program(), closure, extended, limits=LIMITS)
    fresh = evaluate_range(ban_program(), extended, limits=LIMITS)
    assert list(updated.closure.derivations) == list(fresh.derivations)
    assert updated.closure.answers(TypedFact("ok", ["a"])).intervals == (Interval(0, 3),)
    assert updated.recomputed_segments >= 1
    assert verify_closure(ban_program(), updated.closure, limits=LIMITS)


def test_point_range_parity_at_boundaries_and_within_segments():
    program = ban_program()
    closure = evaluate_range(program, base_ban(), limits=LIMITS)
    for point in (0, 2, 4, 5, 7, 9):
        active = [item.fact for item in base_ban() if point in item.valid]
        fresh = set(GenericEngine().evaluate_stratified(
            program, active, limits=LIMITS).result.facts)
        assert set(closure.at(point)) == fresh


def test_unsupported_inputs_fail_closed():
    with pytest.raises(ReasoningError):
        evaluate_range(ban_program(), ["not-a-fact"], limits=LIMITS)
    with pytest.raises(ReasoningError):
        TemporalDerivation(TypedFact("ok", ["a"]), Interval(0, 5), "ok", ())
    with pytest.raises(ReasoningError):
        evaluate_range(degree_program(), base_ban(), limits=LIMITS, max_supports=0)
    stale = evaluate_range(ban_program(), base_ban(), limits=LIMITS)
    other = GenericProgram([GenericRule(
        "other", [TypedAtom("person", [X])], [], TypedAtom("other", [X]))])
    with pytest.raises(ReasoningError, match="drifted"):
        verify_closure(other, stale, limits=LIMITS)


def test_range_results_are_deterministic():
    first = evaluate_range(ban_program(), base_ban(), limits=LIMITS)
    second = evaluate_range(ban_program(), list(reversed(base_ban())), limits=LIMITS)
    assert first.derivations == second.derivations
    assert first.segments == second.segments
