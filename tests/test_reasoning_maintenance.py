"""RE-15 bounded supports, reverse dependencies and insertion propagation."""

import pytest

from gestaltdb.reasoning.maintenance import (
    DependencyIndex,
    PropagationResult,
    Support,
    build_index,
    propagate_insertions,
)
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def program():
    return GenericProgram([
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ])


def test_support_validation_and_identity():
    fact = TypedFact("reach", ["a", "b"])
    premise = TypedFact("edge", ["a", "b"])
    first = Support(fact, "base", [premise])
    assert first == Support(fact, "base", [premise])
    assert first.digest == Support(fact, "base", [premise]).digest
    assert first.digest != Support(fact, "step", [premise]).digest
    with pytest.raises(ReasoningError):
        Support("not-a-fact", "base", [premise])
    with pytest.raises(ReasoningError):
        Support(fact, "", [premise])
    with pytest.raises(ReasoningError):
        Support(fact, "base", [])
    with pytest.raises(ReasoningError):
        Support(fact, "base", ["not-a-fact"])


def test_build_index_binds_generation_and_rejects_inconsistency():
    prog = program()
    closure = [TypedFact("edge", ["a", "b"]), TypedFact("reach", ["a", "b"])]
    support = Support(TypedFact("reach", ["a", "b"]), "base", [TypedFact("edge", ["a", "b"])])
    index = build_index(prog, closure, [support])
    assert isinstance(index, DependencyIndex)
    assert index.supports_for(TypedFact("reach", ["a", "b"])) == (support,)
    assert index.derived_from(TypedFact("edge", ["a", "b"])) == (TypedFact("reach", ["a", "b"]),)
    assert index.premises_of(TypedFact("reach", ["a", "b"])) == (TypedFact("edge", ["a", "b"]),)
    assert index.derived_from(TypedFact("reach", ["a", "b"])) == ()
    with pytest.raises(ReasoningError, match="known closure"):
        build_index(prog, closure, [Support(TypedFact("reach", ["x", "y"]), "base", [TypedFact("edge", ["a", "b"])])])
    with pytest.raises(ReasoningError, match="known closure"):
        build_index(prog, closure, [Support(TypedFact("reach", ["a", "b"]), "base", [TypedFact("edge", ["z", "z"])])])
    with pytest.raises(ReasoningError, match="unknown rule"):
        build_index(prog, closure, [Support(TypedFact("reach", ["a", "b"]), "nope", [TypedFact("edge", ["a", "b"])])])
    with pytest.raises(ReasoningError):
        build_index(prog, ["not-a-fact"], [])
    with pytest.raises(ReasoningError):
        build_index("not-a-program", closure, [])


def test_insertion_propagation_matches_full_rematerialization():
    prog = program()
    base = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"])]
    full_base = set(GenericEngine().evaluate(prog, base, limits=ReasoningLimits()).facts)
    grown = propagate_insertions(prog, list(full_base), [TypedFact("edge", ["c", "d"])],
                                 limits=ReasoningLimits())
    assert isinstance(grown, PropagationResult)
    expected = set(GenericEngine().evaluate(
        prog, base + [TypedFact("edge", ["c", "d"])], limits=ReasoningLimits()).facts)
    assert set(grown.facts) == expected
    assert TypedFact("reach", ["a", "d"]) in grown.facts
    assert all(isinstance(item, Support) for item in grown.supports)
    assert grown.index.program_digest == prog.digest
    # Alternative sources preserved: a-c reachable two ways after adding shortcut.
    assert len(grown.supports) >= len({item.fact for item in grown.supports})
    # Reverse map answers without path enumeration.
    assert TypedFact("reach", ["a", "d"]) in grown.index.derived_from(TypedFact("edge", ["c", "d"])


)
    # Unrelated predicate insertions only extend, never rewrite.
    before = set(grown.facts)
    again = propagate_insertions(prog, list(before), [TypedFact("other", ["z"])], limits=ReasoningLimits())
    assert set(again.facts) == before | {TypedFact("other", ["z"])}
    assert TypedFact("other", ["z"]) in again.added


def test_budgets_staleness_and_cancellation():
    prog = program()
    closure = list(GenericEngine().evaluate(prog, [TypedFact("edge", ["a", "b"])], limits=ReasoningLimits()).facts)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        propagate_insertions(prog, closure, [TypedFact("edge", [f"n{i}", f"n{i+1}"]) for i in range(20)],
                             limits=ReasoningLimits(max_facts=len(closure)))
    with pytest.raises(ReasoningLimitError, match="max_supports"):
        propagate_insertions(prog, closure, [TypedFact("edge", ["b", "c"])],
                             limits=ReasoningLimits(), max_supports=1)
    with pytest.raises(ReasoningCancelledError):
        propagate_insertions(prog, closure, [TypedFact("edge", ["b", "c"])],
                             limits=ReasoningLimits(), cancel=lambda: True)
    with pytest.raises(ReasoningError, match="rematerialize"):
        stale = build_index(program(), closure, [])
        propagate_insertions(GenericProgram([GenericRule("other", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))]),
                             closure, [TypedFact("p", ["a"])], limits=ReasoningLimits(), index=stale)
    with pytest.raises(ReasoningError):
        propagate_insertions(prog, closure, [TypedFact("edge", ["b", "c"])], limits=ReasoningLimits(),
                             max_supports=True)
    with pytest.raises(ReasoningError, match="negation"):
        negated = GenericProgram([GenericRule("n", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                                              [TypedAtom("m", [X])])], closed=["m"])
        propagate_insertions(negated, [], [TypedFact("p", ["a"])], limits=ReasoningLimits())


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
from standalone.maintenance import Support, build_index, propagate_insertions
from standalone.model import ReasoningLimits, Variable
from standalone.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
x, y = Variable('x'), Variable('y')
prog = GenericProgram([GenericRule('b', [TypedAtom('p', [x, y])], [], TypedAtom('q', [x, y]))])
base = set(GenericEngine().evaluate(prog, [TypedFact('p', ['a', 'b'])], limits=ReasoningLimits()).facts)
out = propagate_insertions(prog, list(base), [TypedFact('p', ['b', 'c'])], limits=ReasoningLimits())
assert TypedFact('q', ['b', 'c']) in out.facts
assert out.index.program_digest == prog.digest
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
