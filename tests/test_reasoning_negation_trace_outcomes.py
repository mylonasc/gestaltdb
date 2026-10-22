"""A why-not negation trace records present blockers, never absence evidence."""

import pytest

from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import (
    GenericEngine, GenericProgram, GenericRule, NegationWitness, TypedAtom, TypedFact,
)


def run(max_proof_nodes=100):
    x = Variable("x")
    rule = GenericRule("eligible", [TypedAtom("candidate", [x])], [], TypedAtom("eligible", [x]),
                       [TypedAtom("blocked", [x]), TypedAtom("excluded", [x])])
    facts = [TypedFact("candidate", [value]) for value in "abcd"]
    facts += [TypedFact("blocked", ["b"]), TypedFact("excluded", ["c"]),
              TypedFact("blocked", ["d"]), TypedFact("excluded", ["d"])]
    result = GenericEngine().evaluate_stratified(
        GenericProgram([rule], closed=["blocked", "excluded"]), facts,
        limits=ReasoningLimits(max_proof_nodes=max_proof_nodes), collect_negation_trace=True)
    return result, facts


def test_trace_labels_present_blockers_and_does_not_claim_successful_absence():
    result, facts = run()
    assert TypedFact("eligible", ["a"]) in result.result
    assert len(result.witnesses) == 3
    assert {(w.predicate, w.args) for w in result.witnesses} == {
        ("blocked", ("b",)), ("excluded", ("c",)), ("blocked", ("d",)),
    }
    for witness in result.witnesses:
        assert witness.outcome == "present"
        assert TypedFact(witness.predicate, witness.args) in facts


def test_trace_truncation_never_truncates_closure():
    complete, _ = run()
    limited, _ = run(max_proof_nodes=1)
    assert limited.result == complete.result
    assert limited.truncated and len(limited.witnesses) == 1
    assert limited.witnesses[0].outcome == "present"


def test_blocker_trace_cannot_be_constructed_as_absence_evidence():
    with pytest.raises(ReasoningError, match="present"):
        NegationWitness("rule", 0, "p", ["a"], ["p"], "horizon", outcome="absent")
