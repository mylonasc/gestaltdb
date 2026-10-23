"""Literal equality must not depend on positional versus exact access."""

import pytest

from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


@pytest.mark.parametrize("fact_zero,probe_zero", [(0.0, -0.0), (-0.0, 0.0)])
@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("exact", [False, True])
def test_signed_zero_matches_through_every_access_path(fact_zero, probe_zero, position, exact):
    x = Variable("x")
    values = ["a", "a"]
    terms = ["a" if exact else x, "a" if exact else x]
    values[position] = Literal("float", fact_zero)
    terms[position] = Literal("float", probe_zero)
    rule = GenericRule("copy", [TypedAtom("p", terms)], [], TypedAtom("out", ["a"]))
    result = GenericEngine().evaluate(GenericProgram([rule]), [TypedFact("p", values)],
                                       limits=ReasoningLimits())
    assert TypedFact("out", ["a"]) in result


@pytest.mark.parametrize("stored,probe,expected", [
    (Literal("integer", 0), Literal("float", -0.0), False),
    (Literal("boolean", False), Literal("integer", 0), False),
    (Literal("float", float("nan")), Literal("float", float("nan")), False),
    (Literal("null", None), Literal("null", None), True),
])
def test_positional_lookup_preserves_kind_null_and_nan_semantics(stored, probe, expected):
    x = Variable("x")
    rule = GenericRule("copy", [TypedAtom("p", [probe, x])], [], TypedAtom("out", [x]))
    result = GenericEngine().evaluate(GenericProgram([rule]), [TypedFact("p", [stored, "a"])],
                                       limits=ReasoningLimits())
    assert (TypedFact("out", ["a"]) in result) is expected
