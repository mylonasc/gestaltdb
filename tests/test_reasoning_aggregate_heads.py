"""Aggregate heads may refer only to group keys and aggregate targets."""

import pytest

from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import (
    AggregateRule, Aggregation, GenericEngine, GenericProgram, TypedAtom, TypedFact,
)


X, Y, N = map(Variable, ("x", "y", "n"))


@pytest.mark.parametrize("group,head", [([], [X, N]), ([], [Y, N]), ([X], [X, Y, N])])
def test_non_grouped_head_variables_fail_at_construction(group, head):
    with pytest.raises(ReasoningError, match="grouping keys or aggregation targets"):
        AggregateRule("bad", [TypedAtom("p", [X, Y])], group,
                      [Aggregation("count", Y, N)], TypedAtom("out", head))


@pytest.mark.parametrize("group,head", [([], [N]), ([X], [X, N]), ([], ["constant", N])])
def test_grouped_global_and_constant_heads_still_evaluate(group, head):
    rule = AggregateRule("ok", [TypedAtom("p", [X, Y])], group,
                         [Aggregation("count", Y, N)], TypedAtom("out", head))
    result = GenericEngine().evaluate(GenericProgram([], [rule]), [TypedFact("p", ["a", "b"])],
                                       limits=ReasoningLimits())
    assert len([fact for fact in result.facts if fact.predicate == "out"]) == 1
