"""Support discovery binds target heads before enumerating body candidates."""

from gestaltdb.reasoning.maintenance import rematerialize
from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


def test_copy_support_discovery_is_linear_with_an_indexed_target_head():
    x = Variable("x")
    program = GenericProgram([GenericRule("copy", [TypedAtom("p", [x])], [], TypedAtom("q", [x]))])
    base = [TypedFact("p", [f"n{i}"]) for i in range(200)]
    result = rematerialize(program, base, limits=ReasoningLimits(max_candidate_rows=1000))
    assert len(result.facts) == 400
    assert len(result.supports) == 200
    assert result.work <= 201


def test_bound_head_keeps_all_alternative_premises_and_repeated_variables():
    x, y = Variable("x"), Variable("y")
    program = GenericProgram([
        GenericRule("project", [TypedAtom("p", [x, y])], [], TypedAtom("q", [x, x])),
    ])
    base = [TypedFact("p", ["a", "b"]), TypedFact("p", ["a", "c"]), TypedFact("p", ["d", "b"])]
    result = rematerialize(program, base, limits=ReasoningLimits())
    supports = result.index.supports_for(TypedFact("q", ["a", "a"]))
    assert {s.premises for s in supports} == {
        (TypedFact("p", ["a", "b"]),), (TypedFact("p", ["a", "c"]),),
    }
    assert result.index.supports_for(TypedFact("q", ["a", "d"])) == ()
