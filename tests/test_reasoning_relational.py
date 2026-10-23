"""RE-01 additive contracts, invalid inputs, cache isolation and v1 compatibility."""

from dataclasses import FrozenInstanceError, replace
import json

import pytest

from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningError, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import (
    CompilationIdentity, RelationalAtom, RelationalProgram, RelationalRule,
    RelationSchema, explain_plan,
)


X, Y = Variable("x"), Variable("y")


def program():
    return Program([Rule("copy", [Atom(X, "p", Y), Atom(X, "p", Y)], Atom(Y, "q", X))])


def identity():
    return CompilationIdentity(program().digest, InputContext("horizon", "world", "ontology", "policy", "mapping"),
                               "dictionary", "positive-binary-entity-v1", "indexed-python-v1")


def test_lowering_preserves_occurrences_constants_and_legacy_execution():
    legacy = program()
    before = legacy.digest
    assert before == "sha256:64523ce1b9654292ee98a3f23b86ba51b4047685e8ac5a57d67fe5a01978cfaa"
    ir = RelationalProgram.from_program(legacy)
    assert ir.digest == "sha256:3eaf5ea6a414523a2d62e439c0bf3c594e2027ab9f2ec286e08dde5e48e50d51"
    assert ir.rules[0].body == (RelationalAtom("p", (X, Y)),) * 2
    assert ir.rules[0].head == RelationalAtom("q", (Y, X))
    assert ir.schemas == (RelationSchema("p", ("subject", "object")), RelationSchema("q", ("subject", "object")))
    assert ir.digest != before == legacy.digest
    assert DatalogEngine().evaluate(legacy, [Fact("?x", "p", "b")], limits=ReasoningLimits()).facts == (
        Fact("?x", "p", "b"), Fact("b", "q", "?x"))


def test_nary_ir_validates_structure_but_is_not_accepted_as_an_executable_program():
    schema = RelationSchema("triple", ["a", "b", "c"])
    atom = RelationalAtom("triple", [X, "?constant", X])
    ir = RelationalProgram([schema], [RelationalRule("r", [atom], atom)])
    assert ir.schemas == (schema,) and atom.variables == frozenset([X])
    with pytest.raises(ReasoningError, match="program"):
        DatalogEngine().evaluate(ir, [], limits=ReasoningLimits())
    with pytest.raises(FrozenInstanceError):
        schema.name = "changed"


@pytest.mark.parametrize("make", [
    lambda: RelationSchema("p", []), lambda: RelationSchema("p", ["a", "a"]),
    lambda: RelationSchema("", ["a"]), lambda: RelationSchema("p", "ab"),
    lambda: RelationSchema("p", [None]), lambda: RelationalAtom("p", []),
    lambda: RelationalAtom("p", [None]), lambda: RelationalAtom("p", [True]),
    lambda: RelationalAtom("p", [42]), lambda: RelationalRule("r", [], RelationalAtom("p", [X])),
    lambda: RelationalRule("r", ["bad"], RelationalAtom("p", [X])),
    lambda: RelationalRule("r", [RelationalAtom("p", [X])], RelationalAtom("p", [Y])),
    lambda: RelationalProgram([RelationSchema("p", ["a"])] * 2),
    lambda: RelationalProgram(["bad"]), lambda: RelationalProgram([], ["bad"]),
    lambda: RelationalProgram([], [RelationalRule("r", [RelationalAtom("p", [X])], RelationalAtom("p", [X]))]),
    lambda: RelationalProgram([RelationSchema("p", ["a", "b"])],
                             [RelationalRule("r", [RelationalAtom("p", [X])], RelationalAtom("p", [X]))]),
    lambda: RelationalProgram.from_program([]),
])
def test_invalid_contracts_fail_early(make):
    with pytest.raises(ReasoningError):
        make()


def test_canonical_catalog_identity_and_order_sensitive_schema():
    a, b = RelationSchema("a", ["x", "y"]), RelationSchema("b", ["x", "y"])
    assert RelationalProgram([a, b]).digest == RelationalProgram([b, a]).digest
    assert RelationalProgram([a]).digest != RelationalProgram([replace(a, columns=("y", "x"))]).digest
    atom = RelationalAtom("a", [X, "?x"])
    rule = RelationalRule("r", [atom], atom)
    with pytest.raises(ReasoningError, match="unique"):
        RelationalProgram([a], [rule, rule])
    assert RelationalProgram([a], [rule]).digest != RelationalProgram([a], [replace(rule, rule_id="s")]).digest


@pytest.mark.parametrize("field", ["context_id", "world", "ontology_revision", "source_policy_id", "mapping_id"])
def test_context_identity_isolation(field):
    first = identity()
    second = replace(first, context=replace(first.context, **{field: "changed"}))
    assert first.digest != second.digest
    with pytest.raises(ReasoningError, match="incompatible"):
        first.require_compatible(second)


@pytest.mark.parametrize("field", ["program_digest", "dictionary_id", "semantics_profile", "executor_version"])
def test_compilation_identity_isolation(field):
    first = identity()
    second = replace(first, **{field: "changed"})
    assert first.digest != second.digest
    with pytest.raises(ReasoningError):
        first.require_compatible(second)
    with pytest.raises(ReasoningError):
        replace(first, **{field: ""})


def test_explain_schema_is_structural_and_detached_from_program_and_inputs():
    plan = explain_plan(program(), identity=identity())
    assert json.loads(json.dumps(plan)) == plan
    assert plan["kind"] == "structural-only"
    assert [a["position"] for a in plan["rules"][0]["body"]] == [0, 1]
    assert all(a["actual_rows"] is None for a in plan["rules"][0]["body"])
    plan["rules"].clear()
    assert explain_plan(program(), identity=identity())["rules"]
    identity().require_compatible(identity())
    with pytest.raises(ReasoningError):
        explain_plan(Program(), identity=identity())
    with pytest.raises(ReasoningError):
        explain_plan(program(), identity=replace(identity(), executor_version="polars-unimplemented"))
    with pytest.raises(ReasoningError):
        replace(identity(), context="unauthenticated")


def test_documented_structural_inspection_example_runs():
    from pathlib import Path
    import textwrap

    text = (Path(__file__).parents[1] / "docs/reasoning-execution.rst").read_text()
    block = text.split(".. code-block:: python\n\n", 1)[1].split("\n\n``explain_plan``", 1)[0]
    exec(compile(textwrap.dedent(block), "reasoning-execution.rst", "exec"), {})
