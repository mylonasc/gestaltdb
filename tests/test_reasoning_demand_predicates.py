"""Predicate pruning preserves recursive frontiers and asserted headed facts."""

import pytest

from gestaltdb.reasoning.demand import BoundGoal, answer, required_predicates
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


def test_predicate_plan_keeps_all_reachable_inputs_not_only_seed_subject():
    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
        GenericRule("unrelated", [TypedAtom("noise", [x])], [], TypedAtom("ignored", [x])),
    ])
    goal = BoundGoal("reach", ["a", y])
    assert required_predicates(program, goal) == ("edge", "reach")
    facts = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]),
             TypedFact("reach", ["a", "asserted"]), TypedFact("noise", ["unused"])]
    selected = [fact for fact in facts if fact.predicate in required_predicates(program, goal)]
    expected = answer(program, facts, goal, limits=ReasoningLimits()).facts
    assert answer(program, selected, goal, limits=ReasoningLimits()).facts == expected
    assert TypedFact("reach", ["a", "c"]) in expected
    assert TypedFact("reach", ["a", "asserted"]) in expected


def test_extensional_goal_keeps_its_input_predicate_and_closed_scopes_fail():
    x = Variable("x")
    assert required_predicates(GenericProgram([]), BoundGoal("p", [x])) == ("p",)
    with pytest.raises(ReasoningError, match="closed"):
        required_predicates(GenericProgram([], closed=["p"]), BoundGoal("p", [x]))
