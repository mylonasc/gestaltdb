"""Tabled frontier completeness, exact evidence and cumulative fail-closed work."""

import random
from itertools import product

import pytest

from gestaltdb.reasoning.engine import DatalogEngine, _unify
from gestaltdb.reasoning.frontier import evaluate_frontier
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.protocols import Evidence

X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def program():
    return Program([
        Rule("base", [Atom(X, "edge", Y)], Atom(X, "reach", Y)),
        Rule("step", [Atom(X, "reach", Y), Atom(Y, "edge", Z)], Atom(X, "reach", Z)),
    ])


def reader(facts, seen=None):
    def read(request):
        if seen is not None:
            seen.append(request)
        for i, fact in enumerate(facts):
            if (fact.predicate == request.predicate
                    and (request.subject is None or fact.subject == request.subject)
                    and (request.object is None or fact.object == request.object)):
                yield Evidence(fact, f"version-{i}")
    return read


def assert_parity(p, facts, goal):
    full = DatalogEngine().evaluate(p, facts, limits=ReasoningLimits())
    # Independent tiny grounding oracle: no indexed matching or demand code.
    closure = set(facts)
    domain = {v for f in facts for v in (f.subject, f.object)}
    domain.update(t for r in p.rules for a in (*r.body, r.head)
                  for t in (a.subject, a.object) if isinstance(t, str))
    while True:
        added = set()
        for rule in p.rules:
            variables = sorted({t for a in (*rule.body, rule.head)
                                for t in (a.subject, a.object) if isinstance(t, Variable)}, key=lambda v: v.name)
            for values in product(sorted(domain), repeat=len(variables)):
                env = dict(zip(variables, values))
                def ground(atom):
                    return Fact(env.get(atom.subject, atom.subject), atom.predicate, env.get(atom.object, atom.object))
                if all(ground(atom) in closure for atom in rule.body):
                    added.add(ground(rule.head))
        if added <= closure:
            break
        closure.update(added)
    assert set(full.facts) == closure
    expected = {fact for fact in full.facts if fact.predicate == goal.predicate and _unify(goal, fact, {}) is not None}
    result = evaluate_frontier(p, goal, reader(facts), limits=ReasoningLimits())
    assert set(result.facts) == expected
    return result


def test_recursive_frontier_and_asserted_heads():
    facts = [Fact("a", "edge", "b"), Fact("b", "edge", "c"), Fact("c", "edge", "a"),
             Fact("a", "reach", "d"), Fact("d", "edge", "e"), Fact("noise", "edge", "outside")]
    seen = []
    result = evaluate_frontier(program(), Atom("a", "reach", Y), reader(facts, seen), limits=ReasoningLimits())
    assert {fact.object for fact in result.facts} == {"a", "b", "c", "d", "e"}
    assert any(r.subject == "d" and r.predicate == "edge" for r in seen)
    assert not any(e.fact.subject == "noise" for e in result.evidence)
    assert len(set(seen)) == len(seen)
    assert_parity(program(), facts, Atom("a", "reach", Y))


@pytest.mark.parametrize("goal", [Atom("a", "reach", Y), Atom(X, "reach", "c"),
                                 Atom(X, "reach", X), Atom("a", "reach", "c"), Atom(X, "reach", Y)])
def test_random_graph_parity(goal):
    rng = random.Random(341)
    for _ in range(35):
        facts = [Fact(a, p, b) for a in "abcd" for b in "abcd" for p in ("edge", "reach")
                 if rng.random() < .15]
        assert_parity(program(), facts, goal)


def test_constants_repeated_atoms_disconnected_and_mutual_recursion():
    p = Program([
        Rule("constant", [Atom(X, "edge", "c"), Atom(X, "edge", "c")], Atom("fixed", "p", X)),
        Rule("disconnected", [Atom(X, "p", Y), Atom(Z, "flag", Z)], Atom(X, "q", Y)),
        Rule("back", [Atom(X, "q", Y)], Atom(X, "p", Y)),
    ])
    facts = [Fact("a", "edge", "c"), Fact("yes", "flag", "yes"), Fact("no", "flag", "other")]
    assert_parity(p, facts, Atom("fixed", "q", Y))
    assert_parity(p, facts, Atom("different", "q", Y))
    assert_parity(Program([]), facts, Atom(X, "flag", X))


@pytest.mark.parametrize("limit", [dict(max_input_facts=1), dict(max_facts=1),
                                   dict(max_iterations=1), dict(max_candidate_rows=1)])
def test_cumulative_limits(limit):
    with pytest.raises(ReasoningLimitError):
        evaluate_frontier(program(), Atom("a", "reach", Y),
                          reader([Fact("a", "edge", "b"), Fact("b", "edge", "c")]),
                          limits=ReasoningLimits(**limit))


def test_request_bound_and_cancellation():
    with pytest.raises(ReasoningLimitError, match="max_requests"):
        evaluate_frontier(program(), Atom("a", "reach", Y), reader([]), limits=ReasoningLimits(), max_requests=1)
    consumed = []
    def read(request):
        for i in range(10):
            consumed.append(i)
            yield Evidence(Fact("a", request.predicate, str(i)), str(i))
    with pytest.raises(ReasoningCancelledError):
        evaluate_frontier(program(), Atom("a", "reach", Y), read, limits=ReasoningLimits(),
                          cancel=lambda: len(consumed) == 2)
    assert consumed == [0, 1]


def test_reader_context_and_evidence_consistency():
    with pytest.raises(ReasoningError, match="outside"):
        evaluate_frontier(program(), Atom("a", "reach", Y),
                          lambda r: [Evidence(Fact("wrong", r.predicate, "x"), "v")], limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="reference changed"):
        evaluate_frontier(program(), Atom("a", "reach", Y),
                          lambda r: [Evidence(Fact("a", r.predicate, "x"), "v"),
                                     Evidence(Fact("a", r.predicate, "y"), "v")], limits=ReasoningLimits())
