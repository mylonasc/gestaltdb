"""ONTO-01 contracts: no solver or embedded backend is required."""

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from gestaltdb.reasoning.model import (
    Atom, EvaluationResult, Fact, Program, ReasoningError, ReasoningLimits,
    Rule, Variable,
)
from gestaltdb.reasoning.protocols import Evidence, InputContext


def _rule(rule_id="copy"):
    x, y = Variable("x"), Variable("y")
    return Rule(rule_id, [Atom(x, "p", y)], Atom(x, "q", y))


def test_variables_are_typed_and_question_mark_strings_remain_constants():
    variable = Variable("x")
    assert variable != "x"
    assert Atom("?x", "?predicate", "?y").variables == frozenset()
    assert Atom(variable, "p", variable).variables == frozenset({variable})
    assert Fact("?x", "?p", "?y").subject == "?x"


@pytest.mark.parametrize("bad", [None, "", 1, True, [], "\ud800"])
def test_named_terms_require_nonempty_utf8_text(bad):
    for build in (lambda: Variable(bad), lambda: Fact(bad, "p", "b"),
                  lambda: Atom("a", bad, "b")):
        with pytest.raises(ReasoningError):
            build()


def test_ground_facts_and_predicates_cannot_be_variables():
    with pytest.raises(ReasoningError):
        Fact(Variable("x"), "p", "b")
    with pytest.raises(ReasoningError):
        Atom("a", Variable("p"), "b")


def test_rule_safety_and_shapes():
    x, y = Variable("x"), Variable("y")
    with pytest.raises(ReasoningError, match="unsafe"):
        Rule("unsafe", [Atom(x, "p", "b")], Atom(y, "q", "b"))
    with pytest.raises(ReasoningError, match="at least one"):
        Rule("empty", [], Atom("a", "p", "b"))
    with pytest.raises(ReasoningError, match="head"):
        Rule("bad", [Atom(x, "p", y)], Fact("a", "q", "b"))
    with pytest.raises(ReasoningError, match="body"):
        Rule("bad", "atoms", Atom("a", "p", "b"))
    assert Rule("ground", [Atom("a", "p", "b")], Atom("a", "q", "b"))


def test_collections_are_copied_and_values_are_immutable():
    body = [Atom(Variable("x"), "p", "b")]
    rule = Rule("r", body, Atom(Variable("x"), "q", "b"))
    body.clear()
    assert len(rule.body) == 1
    rules = [rule]
    program = Program(rules)
    rules.clear()
    assert program.rules == (rule,)
    with pytest.raises(FrozenInstanceError):
        rule.rule_id = "other"
    assert hash(program)


def test_program_identity_is_order_independent_but_preserves_structural_types():
    a, b = _rule("a"), _rule("b")
    assert Program([b, a]).rules == (a, b)
    assert Program([a, b]).digest == Program([b, a]).digest
    constant = Rule("a", [Atom("x", "p", "y")], Atom("x", "q", "y"))
    assert Program([a]).digest != Program([constant]).digest
    assert Program().digest.startswith("sha256:")
    with pytest.raises(ReasoningError, match="unique"):
        Program([a, a])
    with pytest.raises(ReasoningError):
        Program(["rule"])


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, None])
def test_every_counter_requires_a_positive_integer(bad):
    for field in fields(ReasoningLimits):
        with pytest.raises(ReasoningError, match=field.name):
            ReasoningLimits(**{field.name: bad})


def test_complete_result_has_canonical_set_semantics():
    a, b = Fact("a", "p", "b"), Fact("b", "p", "c")
    facts = [b, a, a]
    result = EvaluationResult(facts, 0, Program().digest)
    facts.clear()
    assert result.facts == (a, b)
    for bad in (-1, True, 0.5):
        with pytest.raises(ReasoningError, match="iterations"):
            EvaluationResult([], bad, Program().digest)


def test_context_and_evidence_hold_identifiers_not_database_objects():
    context = InputContext("snapshot:1", "w", "onto:1", "policy:1", "mapping:1")
    evidence = Evidence(Fact("a", "p", "b"), "version:1")
    assert context.world == "w"
    assert evidence.reference_id == "version:1"
    with pytest.raises(ReasoningError):
        InputContext(object(), "w", "o", "s", "m")
    with pytest.raises(ReasoningError):
        Evidence("fact", "v")


def test_core_implementation_imports_only_stdlib_and_inward_contracts():
    # Inspect implementation imports, not sys.modules: gestaltdb.__init__ already
    # eagerly imports other subsystems and this slice does not alter that API.
    root = Path(__file__).parents[1] / "src" / "gestaltdb" / "reasoning"
    allowed = {"__future__", "hashlib", "json", "dataclasses", "typing", "collections", "bisect", "heapq", "types", "array", "itertools"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert (node.level == 1 and node.module in {"model", "protocols", "proofs", "engine", "relational", "batches", "plans", "extraction", "executors", "terms", "typed", "validation", "equality", "demand", "maintenance", "catalog", "intervals", "temporal_closure", "profiles", "el", "scopes", "scale", "explanations"}) or (
                    node.level == 0 and node.module.split(".")[0] in allowed
                ), (path, ast.dump(node))
