"""RE-12 demand specialization: parity, selectivity, caching and fail-closed modes."""

import itertools
import random

import pytest

from gestaltdb.reasoning.demand import BoundGoal, DemandAnswer, SpecializationCache, answer, specialize
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits, Variable
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def run(program, facts, goal, limits=None, **kwargs):
    return answer(GenericProgram(program), facts, goal,
                  limits=limits if limits is not None else ReasoningLimits(**kwargs))


def test_goal_validation_and_unsupported_combinations_fail_closed():
    with pytest.raises(ReasoningError):
        BoundGoal("", [X])
    with pytest.raises(ReasoningError):
        BoundGoal("p", [])
    with pytest.raises(ReasoningError):
        BoundGoal("p", [42])
    assert BoundGoal("p", [X, "a", Literal("integer", 1)]).adornment == "fbb"
    mixed = GenericProgram([GenericRule("a", [TypedAtom("p", [X])], [], TypedAtom("p", [X])),
                            GenericRule("b", [TypedAtom("p", [X, Y])], [], TypedAtom("p", [Y, X]))])
    with pytest.raises(ReasoningError, match="consistent arity"):
        specialize(mixed, BoundGoal("p", ["a"]))
    with pytest.raises(ReasoningError, match="goal arity"):
        specialize(GenericProgram([GenericRule("a", [TypedAtom("p", [X])], [], TypedAtom("p", [X]))]),
                   BoundGoal("p", ["a", "b"]))
    negated = GenericRule("n", [TypedAtom("p", [X])], [], TypedAtom("q", [X]), [TypedAtom("m", [X])])
    with pytest.raises(ReasoningError, match="negation"):
        specialize(GenericProgram([negated]), BoundGoal("q", [X]))
    assert specialize(GenericProgram([]), BoundGoal("q", ["a"])).rules == ()
    with pytest.raises(ReasoningError):
        specialize([], BoundGoal("q", ["a"]))
    with pytest.raises(ReasoningError):
        specialize(GenericProgram([]), "not-a-goal")
    with pytest.raises(ReasoningError):
        answer(GenericProgram([]), [], BoundGoal("q", ["a"]), limits=None)
    with pytest.raises(ReasoningError):
        answer(GenericProgram([]), ["not-a-fact"], BoundGoal("q", ["a"]), limits=ReasoningLimits())
    with pytest.raises(ReasoningCancelledError):
        answer(GenericProgram([]), [], BoundGoal("q", ["a"]), limits=ReasoningLimits(), cancel=lambda: True)
    with pytest.raises(ReasoningError):
        answer(GenericProgram([]), [], BoundGoal("q", ["a"]), limits=ReasoningLimits(), cache="nope")


def test_selective_hierarchy_avoids_irrelevant_branches():
    chain = [TypedFact("edge", [f"n{i}", f"n{i+1}"]) for i in range(30)]
    other = [TypedFact("edge", [f"m{i}", f"m{i+1}"]) for i in range(30)]
    program = [GenericRule("step", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
               GenericRule("walk", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                             TypedAtom("reach", [X, Z]))]
    scoped = run(program, chain + other, BoundGoal("reach", ["n0", Y]))
    assert all(fact.values[0].startswith("n") for fact in scoped.facts)
    assert TypedFact("reach", ["n0", "n30"]) in scoped.facts
    assert isinstance(scoped, DemandAnswer) and scoped.program.digest != GenericProgram(program).digest
    assert len(scoped.facts) == 30
    assert scoped == run(program, list(reversed(chain + other)), BoundGoal("reach", ["n0", Y]))
    full = GenericEngine().evaluate(GenericProgram(program), chain + other, limits=ReasoningLimits())
    assert set(scoped.facts) <= set(full.facts)


def test_generated_demands_match_full_closure_answers():
    rng = random.Random(20261023)
    literals = [Literal("integer", 0), Literal("integer", 1)]
    for case in range(40):
        domain = [f"e{i}" for i in range(4)]
        arity_of = {"p": rng.randint(1, 2), "q": rng.randint(1, 2)}
        facts = [TypedFact(name, [rng.choice(domain + literals) for _ in range(arity_of[name])])
                 for name in ("p", "q") for _ in range(4)]
        rules = []
        for number in range(3):
            name = rng.choice("pq")
            body = [TypedAtom(other, [rng.choice([X, Y, *domain, *literals])
                                      for _ in range(arity_of[other])])
                    for other in [rng.choice("pq") for _ in range(rng.randint(1, 2))]]
            bound = {term.name for atom in body for term in atom.args if isinstance(term, Variable)}
            if not bound:
                continue
            heads = [domain[0], *[Variable(n) for n in sorted(bound)]]
            rules.append(GenericRule(str(number), body, [],
                                     TypedAtom(name, [rng.choice(heads) for _ in range(arity_of[name])])))
        program = GenericProgram(rules)
        goal_predicate = rng.choice("pq")
        goal = BoundGoal(goal_predicate, [rng.choice(domain + [X, literals[0]])
                                          for _ in range(arity_of[goal_predicate])])
        scoped = answer(program, facts, goal, limits=ReasoningLimits())
        full = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
        expected = []
        for fact in full.facts:
            if fact.predicate != goal_predicate or len(fact.values) != len(goal.args):
                continue
            bindings = {}
            ok = True
            for term, value in zip(goal.args, fact.values):
                if isinstance(term, Variable):
                    if term in bindings and bindings[term] != value:
                        ok = False
                        break
                    bindings[term] = value
                elif isinstance(term, Literal):
                    if not isinstance(value, Literal) or value != term:
                        ok = False
                        break
                elif term != value:
                    ok = False
                    break
            if ok:
                expected.append(fact)
        assert set(scoped.facts) == set(expected), case
        assert scoped == answer(program, facts, goal, limits=ReasoningLimits())


def test_specialization_cache_is_identity_bound_and_deterministic():
    program = GenericProgram([GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))])
    goal = BoundGoal("q", ["a"])
    first = specialize(program, goal)
    assert first == specialize(program, goal)
    cache = SpecializationCache()
    assert cache.specialize(program, goal) == first
    updated, cache = cache.remembering(program, goal)
    assert updated == first and len(cache.entries) == 1
    assert cache.specialize(program, goal) is updated
    # Same adornment pattern, different constants: identical specialization.
    assert cache.remembering(program, BoundGoal("q", ["b"]))[0] == first
    # Different adornment: different specialization.
    assert cache.remembering(program, BoundGoal("q", [X]))[0] != first
    with pytest.raises(ReasoningError):
        cache.specialize(program, "not-a-goal")
    with pytest.raises(ReasoningError):
        SpecializationCache(entries="nope")
    result = answer(program, [TypedFact("p", ["a"]), TypedFact("p", ["b"])], goal,
                    limits=ReasoningLimits(), cache=cache)
    assert result.facts == (TypedFact("q", ["a"]),)


def test_literal_goals_and_limits_behave():
    facts = [TypedFact("age", ["a", Literal("integer", 30)]), TypedFact("age", ["b", Literal("integer", 12)])]
    program = GenericProgram([GenericRule("copy", [TypedAtom("age", [X, Y])], [], TypedAtom("known", [X, Y]))])
    scoped = run(program.rules, facts, BoundGoal("known", ["a", Literal("integer", 30)]))
    assert scoped.facts == (TypedFact("known", ["a", Literal("integer", 30)]),)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        run(program.rules, facts, BoundGoal("known", [X, Y]), max_facts=1)
    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        run(program.rules, facts, BoundGoal("known", [X, Y]), max_input_facts=1)


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone import Variable
from standalone.demand import BoundGoal, SpecializationCache, answer
from standalone.model import ReasoningLimits
from standalone.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
x = Variable('x')
program = GenericProgram([GenericRule('r', [TypedAtom('p', [x])], [], TypedAtom('q', [x]))])
out = answer(program, [TypedFact('p', ['a'])], BoundGoal('q', ['a']), limits=ReasoningLimits())
assert out.facts == (TypedFact('q', ['a']),)
assert SpecializationCache().specialize(program, BoundGoal('q', ['a'])) == out.program
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
