"""RE-20 range closure over change segments with per-interval evidence."""

import random

import pytest

from gestaltdb.reasoning.equality import EqualityScope, FunctionalProperty, SameAs, canonicalize
from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.temporal_closure import (
    TemporalClosure,
    TemporalDerivation,
    TemporalFact,
    canonicalize_segments,
    change_segments,
    evaluate_range,
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


def test_temporal_fact_validation_and_segments():
    fact = TypedFact("edge", ["a", "b"])
    assert TemporalFact(fact, span(0, 5)).fact == fact
    with pytest.raises(ReasoningError):
        TemporalFact("nope", span(0, 5))
    with pytest.raises(ReasoningError):
        TemporalFact(fact, "nope")
    with pytest.raises(ReasoningError):
        TemporalFact(fact, IntervalSet([]))
    assert change_segments([]) == ()
    assert change_segments([TemporalFact(fact, span(0, 5))]) == (Interval(0, 5),)
    assert change_segments([TemporalFact(fact, IntervalSet([Interval(0, 2), Interval(5, 9)]))]) == (
        Interval(0, 2), Interval(2, 5), Interval(5, 9))  # gaps tile uniformly too
    unbounded = change_segments([TemporalFact(fact, IntervalSet([Interval(None, 5)])),
                                 TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, None)]))])
    assert unbounded == (Interval(None, 5), Interval(5, None))
    with pytest.raises(ReasoningError):
        change_segments(["nope"])
    with pytest.raises(ReasoningError):
        change_segments([TemporalFact(fact, span(0, 5))], max_segments=True)
    with pytest.raises(ReasoningLimitError, match="max_segments"):
        change_segments([TemporalFact(TypedFact("p", [str(i)]), span(i, i + 1)) for i in range(5)],
                        max_segments=2)


def test_range_union_matches_change_point_evaluation():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 10)),
             TemporalFact(TypedFact("edge", ["b", "c"]), span(5, 15))]
    closure = evaluate_range(prog, facts, limits=ReasoningLimits())
    assert isinstance(closure, TemporalClosure)
    assert closure.segments == (Interval(0, 5), Interval(5, 10), Interval(10, 15))
    assert closure.answers(TypedFact("reach", ["a", "c"])) == span(5, 10)
    assert closure.answers(TypedFact("reach", ["a", "b"])) == span(0, 10)
    assert closure.answers(TypedFact("reach", ["zzz"])) == IntervalSet([])
    assert set(closure.at(3)) == {TypedFact("edge", ["a", "b"]), TypedFact("reach", ["a", "b"])}
    assert TypedFact("reach", ["a", "c"]) in closure.at(7)
    assert TypedFact("reach", ["a", "c"]) not in closure.at(12)
    evidence = closure.evidence(TypedFact("reach", ["a", "c"]))
    assert len(evidence) == 1 and isinstance(evidence[0], TemporalDerivation)
    assert evidence[0].segment == Interval(5, 10)
    assert evidence[0].rule_id == "step" and len(evidence[0].premises) == 2
    assert closure.evidence(TypedFact("edge", ["a", "b"])) == ()
    with pytest.raises(ReasoningError):
        closure.answers("nope")
    with pytest.raises(ReasoningError):
        closure.at(True)


def test_point_queries_agree_with_independent_point_evaluation():
    rng = random.Random(20261024)
    for case in range(25):
        prog = chain_program()
        domain = [f"e{i}" for i in range(4)]
        facts = [TemporalFact(TypedFact("edge", [rng.choice(domain), rng.choice(domain)]),
                              span(rng.randint(0, 6), rng.randint(7, 12))) for _ in range(6)]
        closure = evaluate_range(prog, facts, limits=ReasoningLimits())
        for point in (0, 3, 6, 9, 11):
            active = [item.fact for item in facts if point in item.valid]
            expected = set(GenericEngine().evaluate(prog, active, limits=ReasoningLimits()).facts)
            assert set(closure.at(point)) == expected, (case, point)
        for item in closure.derivations:
            assert item.segment in closure.segments
            assert all(premise in closure.at(_mid(item.segment)) for premise in item.premises)


def _mid(segment):
    if segment.start is not None:
        return segment.start
    return segment.end - 1 if segment.end is not None else 0


def test_temporal_negation_and_budgets_fail_closed():
    prog = chain_program()
    facts = [TemporalFact(TypedFact("edge", ["a", "b"]), span(0, 5))]
    negated = GenericProgram([GenericRule("n", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                                          [TypedAtom("m", [X])])], closed=["m"])
    with pytest.raises(ReasoningError, match="negation"):
        evaluate_range(negated, [], limits=ReasoningLimits())
    with pytest.raises(ReasoningCancelledError):
        evaluate_range(prog, facts, limits=ReasoningLimits(), cancel=lambda: True)
    crowded = [TemporalFact(TypedFact("edge", [f"n{i}"]), span(i, i + 1)) for i in range(4)]
    with pytest.raises(ReasoningLimitError, match="max_segments"):
        evaluate_range(prog, crowded, limits=ReasoningLimits(), max_segments=2)
    with pytest.raises(ReasoningError):
        evaluate_range(prog, ["nope"], limits=ReasoningLimits())
    with pytest.raises(ReasoningError):
        evaluate_range("nope", facts, limits=ReasoningLimits())


def test_equality_rebuilds_per_segment_without_global_merges():
    seg0 = [TypedFact("mbox", ["n", "a@x"]), TypedFact("mbox", ["n", "b@x"])]
    seg1 = [TypedFact("mbox", ["n", "a@x"])]
    mappings = canonicalize_segments([seg0, seg1], [FunctionalProperty("mbox")], scope_prefix="snap")
    assert len(mappings) == 2
    assert mappings[0].representative("b@x") == "a@x"
    assert mappings[1].representative("b@x") == "b@x"
    assert mappings[0].scope != mappings[1].scope
    assert mappings[0].scope == EqualityScope("snap@0")
    aliased = canonicalize_segments([seg0, seg0], [SameAs("a@x", "b@x")], scope_prefix="snap")
    assert aliased[0].representative("b@x") == aliased[1].representative("b@x") == "a@x"
    assert aliased[0] != aliased[1]  # scopes differ even when classes agree
    with pytest.raises(ReasoningError):
        canonicalize_segments([seg0], [], scope_prefix="")
    with pytest.raises(ReasoningError):
        canonicalize_segments("nope", [], scope_prefix="s")
    assert canonicalize_segments([], [], scope_prefix="s") == ()


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""\
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.intervals import Interval, IntervalSet
from standalone.model import ReasoningLimits, Variable
from standalone.temporal_closure import TemporalFact, change_segments, evaluate_range
from standalone.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
x, y = Variable('x'), Variable('y')
prog = GenericProgram([GenericRule('b', [TypedAtom('p', [x, y])], [], TypedAtom('q', [x, y]))])
facts = [TemporalFact(TypedFact('p', ['a', 'b']), IntervalSet([Interval(0, 4)]))]
closure = evaluate_range(prog, facts, limits=ReasoningLimits())
assert closure.answers(TypedFact('q', ['a', 'b'])).intervals == (Interval(0, 4),)
assert change_segments(facts) == (Interval(0, 4),)
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
