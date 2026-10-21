"""RE-09 explicit negatives and evidence-bearing validation reports."""

import pytest

from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimits
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
from gestaltdb.reasoning.validation import (
    Denial,
    ValidationReport,
    ValidationSuite,
    Violation,
    find_unsatisfiable,
    validate,
)
from gestaltdb.reasoning.model import Variable


X, Y = Variable("x"), Variable("y")
TYPE = "type"


def test_suite_validation_rejects_malformed_scopes():
    with pytest.raises(ReasoningError):
        ValidationSuite("")
    with pytest.raises(ReasoningError):
        ValidationSuite(TYPE, denials=["not-a-denial"])
    with pytest.raises(ReasoningError):
        ValidationSuite(TYPE, disjoint=[("A",)])
    with pytest.raises(ReasoningError):
        ValidationSuite(TYPE, disjoint=[("A", "A")])
    with pytest.raises(ReasoningError):
        ValidationSuite(TYPE, irreflexive=[True])
    with pytest.raises(ReasoningError):
        Denial("p", ["ok", 42])
    with pytest.raises(ReasoningError):
        Violation("unknown-kind", (TypedFact("p", ["a"]),), ("s",), "d")
    with pytest.raises(ReasoningError):
        Violation("denied-positive", (), ("s",), "d")
    assert ValidationSuite(TYPE, disjoint=[("B", "A"), ("A", "B")]).disjoint == (("A", "B"),)
    assert ValidationSuite(TYPE).digest != ValidationSuite(TYPE, irreflexive=["p"]).digest


def test_denied_positives_disjointness_irreflexivity_and_functionality():
    facts = [TypedFact(TYPE, ["n", "A"]), TypedFact(TYPE, ["n", "B"]),
             TypedFact("likes", ["n", "n"]), TypedFact("age", ["n", Literal("integer", 30)]),
             TypedFact("name", ["n", "ann"]), TypedFact("name", ["n", "beth"])]
    suite = ValidationSuite(TYPE, denials=[Denial("likes", ["n", "m"])],
                            disjoint=[("A", "B")], irreflexive=["likes"], functional=["age", "name"])
    report = validate(facts, suite)
    assert not report.clean
    assert report.scanned_facts == len(facts)
    kinds = {violation.kind for violation in report.violations}
    assert kinds == {"disjoint-members", "reflexive-loop", "functional-conflict"}
    by_kind = {violation.kind: violation for violation in report.violations}
    assert by_kind["disjoint-members"].sources == ("disjoint:A:B",)
    assert by_kind["reflexive-loop"].facts == (TypedFact("likes", ["n", "n"]),)
    assert by_kind["functional-conflict"].sources == ("functional:name",)
    assert len(by_kind["functional-conflict"].facts) == 2
    # A matching denial fires; an unmatched denial stays silent.
    denied = validate(facts, ValidationSuite(TYPE, denials=[Denial("likes", ["n", "n"])]))
    assert [violation.kind for violation in denied.violations] == ["denied-positive"]
    assert validate(facts, ValidationSuite(TYPE)).clean


def test_reports_are_bounded_deterministic_and_cancellable():
    facts = [TypedFact(TYPE, [f"n{i}", "A"]) for i in range(10)]
    suite = ValidationSuite(TYPE, disjoint=[("A", "B")])
    assert validate(facts, suite).clean
    both = [TypedFact(TYPE, [f"n{i}", "B"]) for i in range(5)]
    first = validate(facts + both, suite, max_violations=2)
    assert len(first.violations) == 2 and first.truncated and not first.clean
    assert first == validate(list(reversed(facts + both)), suite, max_violations=2)
    with pytest.raises(ReasoningCancelledError):
        validate(facts, suite, cancel=lambda: True)
    with pytest.raises(ReasoningError):
        validate(["not-a-fact"], suite)
    with pytest.raises(ReasoningError):
        validate(facts, "not-a-suite")
    with pytest.raises(ReasoningError):
        validate(facts, suite, max_violations=0)
    limited = validate(facts + both, suite, max_violations=100)
    assert len(limited.violations) == 5 and not limited.truncated


def test_unsatisfiable_classes_need_no_instance_data():
    assert find_unsatisfiable([("C", "D"), ("C", "E")], [("D", "E")]) == ("C",)
    assert find_unsatisfiable([("C", "D"), ("D", "E")], [("C", "E")]) == ("C",)
    assert find_unsatisfiable([("C", "D")], [("D", "E")]) == ()
    assert find_unsatisfiable([], []) == ()
    with pytest.raises(ReasoningError):
        find_unsatisfiable([("C", "")], [])
    with pytest.raises(ReasoningError):
        find_unsatisfiable([], [("A", "A")])
    with pytest.raises(ReasoningError):
        find_unsatisfiable("nope", [])


def test_validation_reads_generic_closure_without_changing_it():
    program = GenericProgram([GenericRule("copy", [TypedAtom("p", [X])], [], TypedAtom("q", [X]))])
    closure = GenericEngine().evaluate(program, [TypedFact("p", ["a"])], limits=ReasoningLimits())
    report = validate(closure.facts, ValidationSuite(TYPE, denials=[Denial("q", ["a"])]))
    assert [violation.kind for violation in report.violations] == ["denied-positive"]
    assert report.violations[0].facts == (TypedFact("q", ["a"]),)
    assert isinstance(report, ValidationReport) and report.scanned_facts == 2


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
from standalone.validation import ValidationSuite, find_unsatisfiable, validate
from standalone.typed import TypedFact
report = validate([TypedFact('type', ['n', 'A']), TypedFact('type', ['n', 'B'])],
                  ValidationSuite('type', disjoint=[('A', 'B')]))
assert [v.kind for v in report.violations] == ['disjoint-members']
assert find_unsatisfiable([('C', 'D')], [('D', 'E')]) == ()
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
