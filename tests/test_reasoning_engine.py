"""Standalone closure and bounds, with an independent grounding oracle."""

import itertools
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import (
    Atom, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits,
    Rule, Variable,
)


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def _evaluate(rules=(), facts=(), *, collect_proofs=False, **limits):
    return DatalogEngine().evaluate(Program(rules), facts, limits=ReasoningLimits(**limits),
                                    collect_proofs=collect_proofs)


def _copy(source="p", target="q", rule_id="copy"):
    return Rule(rule_id, [Atom(X, source, Y)], Atom(X, target, Y))


def _transitive():
    return Rule("transitive", [Atom(X, "p", Y), Atom(Y, "p", Z)], Atom(X, "p", Z))


def _reference(program, inputs):
    """Ground all substitutions over the finite named domain; no engine helpers."""
    closure = set(inputs)
    domain = {value for fact in inputs for value in (fact.subject, fact.object)}
    for rule in program.rules:
        for atom in (*rule.body, rule.head):
            domain.update(term for term in (atom.subject, atom.object) if isinstance(term, str))
    while True:
        added = set()
        for rule in program.rules:
            variables = sorted({term.name for atom in rule.body
                                for term in (atom.subject, atom.object)
                                if isinstance(term, Variable)})
            for values in itertools.product(sorted(domain), repeat=len(variables)):
                substitution = dict(zip(variables, values))

                def ground(atom):
                    def term(value):
                        return substitution[value.name] if isinstance(value, Variable) else value
                    return Fact(term(atom.subject), atom.predicate, term(atom.object))

                if all(ground(atom) in closure for atom in rule.body):
                    added.add(ground(rule.head))
        if added <= closure:
            return closure
        closure.update(added)


def test_empty_inputs_and_irrelevant_program_need_no_rounds():
    assert _evaluate([_copy()]).facts == ()
    assert _evaluate([_copy()]).iterations == 0
    fact = Fact("a", "unused", "b")
    assert _evaluate(facts=[fact, fact]).facts == (fact,)
    assert _evaluate([_copy()], [fact]).iterations == 0
    missing = Rule("missing", [Atom(X, "p", Y), Atom(X, "missing", Y)], Atom(X, "q", Y))
    assert _evaluate([missing], [Fact("a", "p", "b")]).iterations == 0


def test_recursive_closure_is_set_valued_and_deterministic():
    inputs = [Fact("a", "p", "b"), Fact("b", "p", "c"), Fact("c", "p", "a")]
    expected = tuple(Fact(a, "p", b) for a in "abc" for b in "abc")
    first = _evaluate([_transitive()], inputs)
    assert first.facts == expected
    assert first == _evaluate([_transitive()], list(reversed(inputs)) + inputs)
    assert set(first.facts) == _reference(Program([_transitive()]), inputs)


def test_constants_repeated_variables_ground_heads_and_question_mark_terms():
    rules = [
        Rule("same", [Atom(X, "p", X)], Atom(X, "same", X)),
        Rule("ground", [Atom("a", "p", "a")], Atom("?x", "?p", "?y")),
        Rule("constant", [Atom(X, "p", "b")], Atom(X, "points-to", "constant")),
    ]
    inputs = [Fact("a", "p", "a"), Fact("a", "p", "b")]
    result = _evaluate(rules, inputs)
    assert Fact("a", "same", "a") in result.facts
    assert Fact("a", "same", "b") not in result.facts
    assert Fact("?x", "?p", "?y") in result.facts
    assert Fact("a", "points-to", "constant") in result.facts
    assert set(result.facts) == _reference(Program(rules), inputs)


def test_disconnected_and_repeated_atoms_join_correctly():
    rule = Rule("join", [Atom(X, "p", Y), Atom(X, "p", Y), Atom("c", "q", "d")],
                Atom(Y, "reverse", X))
    inputs = [Fact("a", "p", "b"), Fact("c", "q", "d")]
    assert set(_evaluate([rule], inputs).facts) == _reference(Program([rule]), inputs)


def test_late_body_delta_can_enable_an_old_prefix():
    rules = [_copy("seed", "late"),
             Rule("join", [Atom(X, "old", Y), Atom(Y, "late", Z)], Atom(X, "out", Z))]
    result = _evaluate(rules, [Fact("a", "old", "b"), Fact("b", "seed", "c")])
    assert Fact("a", "out", "c") in result.facts


@pytest.mark.parametrize("collect_proofs", [False, True])
def test_generated_small_programs_match_independent_grounding(collect_proofs):
    rng = random.Random(20261019)
    terms = [X, Y, Z, "a", "b"]
    for case in range(100):
        inputs = [Fact(rng.choice("ab"), rng.choice("pq"), rng.choice("ab")) for _ in range(5)]
        rules = []
        for index in range(4):
            body = [Atom(rng.choice(terms), rng.choice("pq"), rng.choice(terms))
                    for _ in range(rng.randint(1, 3))]
            bound = sorted({term.name for atom in body for term in (atom.subject, atom.object)
                            if isinstance(term, Variable)})
            head_terms = ["a", "b"] + [Variable(name) for name in bound]
            rules.append(Rule(str(index), body,
                              Atom(rng.choice(head_terms), rng.choice("pq"), rng.choice(head_terms))))
        result = _evaluate(rules, inputs, collect_proofs=collect_proofs)
        assert set(result.facts) == _reference(Program(rules), inputs), case


def test_input_overflow_reads_only_one_extra_item_even_for_duplicates():
    seen = []

    def facts():
        for index in itertools.count():
            seen.append(index)
            yield Fact("a", "p", "b")

    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        _evaluate(facts=facts(), max_input_facts=2)
    assert seen == [0, 1, 2]


def test_rule_limit_fails_before_consuming_input():
    def forbidden():
        raise AssertionError("input should not be touched")
        yield

    with pytest.raises(ReasoningLimitError, match="max_rules"):
        _evaluate([_copy(rule_id="1"), _copy(rule_id="2")], forbidden(), max_rules=1)


def test_input_and_derived_fact_limits_and_exact_bound():
    inputs = [Fact("a", "p", "b"), Fact("b", "p", "c")]
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        _evaluate(facts=inputs, max_facts=1)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        _evaluate([_copy()], inputs, max_facts=3)
    assert len(_evaluate([_copy()], inputs, max_facts=4).facts) == 4


def test_iteration_limit_fails_closed_and_does_not_poison_engine():
    rules = Program([_copy("p", "q", "1"), _copy("q", "r", "2")])
    inputs = [Fact("a", "p", "b")]
    engine = DatalogEngine()
    with pytest.raises(ReasoningLimitError, match="max_iterations"):
        engine.evaluate(rules, inputs, limits=ReasoningLimits(max_iterations=1))
    result = engine.evaluate(rules, inputs, limits=ReasoningLimits(max_iterations=2))
    assert result.iterations == 2
    assert Fact("a", "r", "b") in result.facts
    assert rules.rules[0].body[0].predicate == "p"
    assert inputs == [Fact("a", "p", "b")]


def test_candidate_budget_counts_failed_unifications_and_stops_cartesian_work():
    same = Rule("same", [Atom(X, "p", X)], Atom(X, "same", X))
    assert len(_evaluate([same], [Fact("a", "p", "b")], max_candidate_rows=1).facts) == 1
    with pytest.raises(ReasoningLimitError, match="max_candidate_rows"):
        _evaluate([same], [Fact("a", "p", "b"), Fact("b", "p", "a")], max_candidate_rows=1)
    cross = Rule("cross", [Atom(X, "p", "b"), Atom(Y, "q", "d")], Atom(X, "out", Y))
    inputs = [Fact(str(i), p, o) for i in range(30) for p, o in [("p", "b"), ("q", "d")]]
    with pytest.raises(ReasoningLimitError, match="max_candidate_rows"):
        _evaluate([cross], inputs, max_candidate_rows=10)


def test_endpoint_indexes_avoid_predicate_bucket_cross_product():
    rule = Rule("select", [Atom(X, "links", Y), Atom(Y, "selected", "yes")], Atom(X, "out", Y))
    inputs = [Fact(str(i), "links", str(i)) for i in range(200)]
    inputs.append(Fact("199", "selected", "yes"))
    result = _evaluate([rule], inputs, max_candidate_rows=205)
    assert Fact("199", "out", "199") in result.facts
    assert len(result.facts) == 202


def test_long_rule_uses_no_python_recursive_body_traversal():
    rule = Rule("long", [Atom("a", "p", "b")] * 1100, Atom("a", "q", "b"))
    result = _evaluate([rule], [Fact("a", "p", "b")], max_candidate_rows=1_300_000)
    assert Fact("a", "q", "b") in result.facts


@pytest.mark.parametrize("facts", [None, ["not a fact"], [Atom(X, "p", Y)]])
def test_invalid_inputs_raise_reasoning_error(facts):
    with pytest.raises(ReasoningError):
        _evaluate(facts=facts)


def test_invalid_program_and_limits_raise_reasoning_error():
    with pytest.raises(ReasoningError, match="program"):
        DatalogEngine().evaluate([], [], limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="limits"):
        DatalogEngine().evaluate(Program(), [], limits=None)


def test_input_iterator_errors_propagate_instead_of_returning_partial_closure():
    def broken():
        yield Fact("a", "p", "b")
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        _evaluate([_copy()], broken())


def test_engine_executes_with_only_stdlib_even_without_package_initializer():
    # Load the reasoning package under an isolated name, bypassing only the
    # pre-existing gestaltdb root initializer. -S disables all site packages.
    path = Path(__file__).parents[1] / "src" / "gestaltdb" / "reasoning" / "__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.engine import DatalogEngine
from standalone.model import Atom, Fact, Program, ReasoningLimits, Rule, Variable
x = Variable('x')
p = Program([Rule('r', [Atom(x, 'p', 'b')], Atom(x, 'q', 'b'))])
r = DatalogEngine().evaluate(p, [Fact('a', 'p', 'b')], limits=ReasoningLimits(), collect_proofs=True)
assert Fact('a', 'q', 'b') in r.facts
assert len(r.explain(Fact('a', 'q', 'b')).nodes) == 2
assert not any(name.startswith('gestaltdb') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_hash_seed_does_not_change_closure_or_rounds():
    script = """
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import *
x, y = Variable('x'), Variable('y')
p = Program([Rule('r', [Atom(x, 'p', y)], Atom(y, 'p', x))])
r = DatalogEngine().evaluate(p, {Fact('a','p','b'), Fact('c','p','b')}, limits=ReasoningLimits(), collect_proofs=True)
print(r)
"""
    outputs = [subprocess.check_output([sys.executable, "-c", script], text=True,
                                      env={**os.environ, "PYTHONHASHSEED": seed})
               for seed in ("1", "17", "42")]
    assert outputs[0] == outputs[1] == outputs[2]
