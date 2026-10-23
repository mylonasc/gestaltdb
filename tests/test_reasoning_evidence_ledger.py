"""Version multiplicity, corrections, tombstones and bounded atomic failure."""

import random

import pytest

from gestaltdb.reasoning.evidence_ledger import AssertionVersion, EvidenceLedger
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError
from gestaltdb.reasoning.typed import TypedFact


def test_independent_assertions_and_atomic_correction():
    fact, other = TypedFact("p", ["a"]), TypedFact("p", ["b"])
    ledger = EvidenceLedger("scope")
    first = ledger.transition(scope_id="scope", assertions=[AssertionVersion("v1", fact)])
    assert first.inserted == (fact,)
    second = first.ledger.transition(scope_id="scope", assertions=[AssertionVersion("v2", fact)])
    assert second.inserted == second.retracted == ()
    third = second.ledger.transition(scope_id="scope", retractions=["v1"])
    assert third.ledger.facts == (fact,) and third.retracted == ()
    corrected = third.ledger.transition(scope_id="scope", assertions=[AssertionVersion("v3", other)], retractions=["v2"])
    assert corrected.inserted == (other,) and corrected.retracted == (fact,)
    retry = corrected.ledger.transition(scope_id="scope", assertions=[AssertionVersion("v3", other)], retractions=["v2"])
    assert retry.ledger == corrected.ledger and retry.inserted == retry.retracted == ()
    retired_retry = retry.ledger.transition(scope_id="scope", assertions=[AssertionVersion("v1", fact)])
    assert retired_retry.ledger == retry.ledger
    resurrected = retry.ledger.transition(scope_id="scope", assertions=[AssertionVersion("v4", fact)])
    assert resurrected.inserted == (fact,)


def test_same_fact_correction_does_not_remove_a_logical_fact():
    fact = TypedFact("p", ["a"])
    ledger = EvidenceLedger("scope", [AssertionVersion("old", fact)])
    changed = ledger.transition(scope_id="scope", assertions=[AssertionVersion("new", fact)], retractions=["old"])
    assert changed.inserted == changed.retracted == ()


def test_rejection_is_atomic_and_limits_include_retired_versions():
    fact = TypedFact("p", ["a"])
    ledger = EvidenceLedger("scope", [AssertionVersion("v", fact)], ["v"])
    with pytest.raises(ReasoningError, match="conflicting"):
        ledger.transition(scope_id="scope", assertions=[AssertionVersion("v", TypedFact("q", ["a"]))])
    with pytest.raises(ReasoningError, match="unknown"):
        ledger.transition(scope_id="scope", retractions=["missing"])
    with pytest.raises(ReasoningError, match="scope"):
        ledger.transition(scope_id="other")
    with pytest.raises(ReasoningLimitError):
        ledger.transition(scope_id="scope", assertions=[AssertionVersion("new", fact)], max_versions=1)
    assert ledger.facts == () and ledger.retired == ("v",)


def test_randomized_version_deltas_match_an_independent_active_dictionary():
    rng, active, history = random.Random(251), {}, {}
    ledger = EvidenceLedger("scope")
    for step in range(200):
        before = set(active.values())
        if active and rng.random() < .5:
            key = rng.choice(sorted(active))
            del active[key]
            transition = ledger.transition(scope_id="scope", retractions=[key])
        else:
            key, fact = f"v{step}", TypedFact("p", [f"n{rng.randrange(8)}"])
            history[key] = active[key] = fact
            transition = ledger.transition(scope_id="scope", assertions=[AssertionVersion(key, fact)])
        assert set(transition.inserted) == set(active.values()) - before
        assert set(transition.retracted) == before - set(active.values())
        ledger = transition.ledger
        assert set(ledger.facts) == set(active.values())
        assert len(ledger.versions) == len(history)
