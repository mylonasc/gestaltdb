"""Temporal caches never reuse conclusions across rule content changes."""

import pytest

from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.temporal_closure import TemporalClosure, TemporalFact, evaluate_range, update_range, verify_closure
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


def program(head):
    x = Variable("x")
    return GenericProgram([GenericRule("same-rule-id", [TypedAtom("p", [x])], [], TypedAtom(head, [x]))])


@pytest.mark.parametrize("changed", [GenericProgram([]), program("r")])
def test_changed_program_rebuilds_even_when_active_inputs_and_rule_ids_are_unchanged(changed):
    original = program("q")
    base = [TemporalFact(TypedFact("p", ["a"]), IntervalSet([Interval(0, 10)]))]
    old = evaluate_range(original, base, limits=ReasoningLimits())
    updated = update_range(changed, old, base, limits=ReasoningLimits())
    fresh = evaluate_range(changed, base, limits=ReasoningLimits())
    assert updated.closure.at(0) == fresh.at(0)
    assert updated.reused_segments == 0
    assert updated.closure.program_digest == changed.digest


def test_unknown_legacy_identity_rebuilds_but_same_program_keeps_reuse():
    rules = program("q")
    base = [TemporalFact(TypedFact("p", ["a"]), IntervalSet([Interval(0, 10)]))]
    old = evaluate_range(rules, base, limits=ReasoningLimits())
    legacy = TemporalClosure(old.segments, old.base, old.derivations)
    assert update_range(rules, legacy, base, limits=ReasoningLimits()).reused_segments == 0
    assert update_range(rules, old, base, limits=ReasoningLimits()).reused_segments == 1


def test_verification_rejects_a_bound_program_mismatch():
    base = [TemporalFact(TypedFact("p", ["a"]), IntervalSet([Interval(0, 10)]))]
    closure = evaluate_range(program("q"), base, limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="program"):
        verify_closure(program("r"), closure, limits=ReasoningLimits())
