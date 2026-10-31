"""#394 declarative structural validation under explicit completeness."""

import pytest

from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError
from gestaltdb.reasoning.profiles import check_construct, subset_constructs
from gestaltdb.reasoning.structural import (
    CardinalityBound,
    DatatypeConstraint,
    ExclusiveGroup,
    RequiredField,
    StructuralReport,
    StructuralSuite,
    StructuralViolation,
    validate_structural,
)
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import TypedFact

TYPE = "type"


def test_suite_rejects_malformed_scopes():
    with pytest.raises(ReasoningError):
        StructuralSuite("")
    with pytest.raises(ReasoningError):
        StructuralSuite(TYPE, closed_classes=["ok"], required=["nope"])
    with pytest.raises(ReasoningError):
        CardinalityBound("C", "p", -1, None)
    with pytest.raises(ReasoningError):
        CardinalityBound("C", "p", 2, 1)
    with pytest.raises(ReasoningError):
        DatatypeConstraint("C", "p", "teleport")
    with pytest.raises(ReasoningError):
        ExclusiveGroup("C", ["only-one"])
    with pytest.raises(ReasoningError):
        ExclusiveGroup("C", ["a", "a"])
    with pytest.raises(ReasoningError):
        ExclusiveGroup("C", ["a", "b"], mode="sometimes")
    with pytest.raises(ReasoningError):
        StructuralViolation("unknown-kind", (TypedFact("p", ["a"]),), ("s",), "d")
    assert StructuralSuite(TYPE).digest != StructuralSuite(TYPE, closed_classes=["A"]).digest


def test_required_and_open_world_silence():
    facts = [TypedFact(TYPE, ["n", "Trade"])]
    suite = StructuralSuite(TYPE, closed_classes=["Trade"],
                            required=[RequiredField("Trade", "tradeId")])
    report = validate_structural(facts, suite)
    assert not report.clean
    assert [item.kind for item in report.violations] == ["missing-required"]
    assert report.violations[0].sources == ('structural:v2:required:["Trade","tradeId"]',)
    # Same shape outside the closed scope stays silent.
    assert validate_structural(
        [TypedFact(TYPE, ["m", "Other"])], suite).clean
    # Empty closed scope checks nothing.
    assert validate_structural(facts, StructuralSuite(TYPE)).clean


def test_cardinality_datatype_and_exclusive_groups():
    facts = [TypedFact(TYPE, ["n", "Trade"]),
             TypedFact("leg", ["n", "l1"]), TypedFact("leg", ["n", "l2"]),
             TypedFact("price", ["n", Literal("integer", 5)]),
             TypedFact("cash", ["n", Literal("integer", 1)]),
             TypedFact("physical", ["n", Literal("integer", 2)])]
    suite = StructuralSuite(
        TYPE, closed_classes=["Trade"],
        cardinalities=[CardinalityBound("Trade", "leg", 0, 1)],
        datatypes=[DatatypeConstraint("Trade", "price", "text")],
        exclusive=[ExclusiveGroup("Trade", ["cash", "physical"], mode="at-most-one")])
    report = validate_structural(facts, suite)
    kinds = {item.kind for item in report.violations}
    assert kinds == {"cardinality-violation", "datatype-violation", "exclusive-violation"}
    by_kind = {item.kind: item for item in report.violations}
    assert by_kind["cardinality-violation"].sources == ('structural:v2:cardinality:["Trade","leg",0,1]',)
    assert by_kind["datatype-violation"].sources == ('structural:v2:datatype:["Trade","price","text"]',)
    assert by_kind["exclusive-violation"].sources == (
        'structural:v2:exclusive:["Trade",["cash","physical"],"at-most-one"]',)
    assert all(facts[0] in v.facts for v in report.violations)
    # Exactly-one-of with nothing present also violates.
    one_of = validate_structural(
        [TypedFact(TYPE, ["m", "Trade"])],
        StructuralSuite(TYPE, closed_classes=["Trade"],
                        exclusive=[ExclusiveGroup("Trade", ["cash", "physical"], mode="one-of")]))
    assert [item.kind for item in one_of.violations] == ["exclusive-violation"]


def test_reports_are_bounded_deterministic_and_cancellable():
    facts = [TypedFact(TYPE, [f"n{i}", "Trade"]) for i in range(10)]
    suite = StructuralSuite(TYPE, closed_classes=["Trade"],
                            required=[RequiredField("Trade", "tradeId")])
    assert validate_structural(facts, suite).clean is False
    first = validate_structural(facts, suite, max_violations=3)
    assert len(first.violations) == 3 and first.truncated and not first.clean
    assert first == validate_structural(list(reversed(facts)), suite, max_violations=3)
    full = validate_structural(facts, suite, max_violations=100)
    assert len(full.violations) == 10 and not full.truncated
    with pytest.raises(ReasoningCancelledError):
        validate_structural(facts, suite, cancel=lambda: True)
    with pytest.raises(ReasoningError):
        validate_structural(["not-a-fact"], suite)
    with pytest.raises(ReasoningError):
        validate_structural(facts, "not-a-suite")
    with pytest.raises(ReasoningError):
        validate_structural(facts, suite, max_violations=0)


def test_validation_never_mutates_or_infers():
    facts = [TypedFact(TYPE, ["n", "Trade"])]
    suite = StructuralSuite(TYPE, closed_classes=["Trade"],
                            required=[RequiredField("Trade", "tradeId")])
    report = validate_structural(facts, suite)
    assert len(facts) == 1 and isinstance(report, StructuralReport)
    assert report.scanned_facts == 1


def test_profile_rows_admit_new_constructs():
    assert check_construct("required-property", "gestaltdb-validation-v1").mechanism == "reasoning.structural"
    assert check_construct("cardinality-bounds", "gestaltdb-validation-v1").mechanism == "reasoning.structural"
    assert check_construct("datatype-check", "gestaltdb-validation-v1").mechanism == "reasoning.structural"
    assert check_construct("exclusivity", "gestaltdb-validation-v1").mechanism == "reasoning.structural"
    subset = subset_constructs("gestaltdb-validation-v1")
    assert {"required-property", "cardinality-bounds", "datatype-check", "exclusivity"} <= set(subset)
    with pytest.raises(ReasoningError):
        check_construct("required-property")


def test_core_runs_without_site_packages():
    import subprocess
    import sys
    from pathlib import Path

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.structural import RequiredField, StructuralSuite, validate_structural
from standalone.typed import TypedFact
report = validate_structural([TypedFact('type', ['n', 'Trade'])],
    StructuralSuite('type', closed_classes=['Trade'], required=[RequiredField('Trade', 'tradeId')]))
assert [v.kind for v in report.violations] == ['missing-required']
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_constraint_identities_distinguish_complete_constraints():
    facts = [TypedFact(TYPE, ["n", "C"])]
    suite = StructuralSuite(
        TYPE, closed_classes=["C"],
        cardinalities=[CardinalityBound("C", "p", 1, 2), CardinalityBound("C", "p", 2, 3)],
        exclusive=[ExclusiveGroup("C", ["a", "b"]), ExclusiveGroup("C", ["a", "c"])])
    report = validate_structural(facts, suite)
    assert len(report.violations) == 4
    assert len({v.sources for v in report.violations}) == 4


def test_constraint_identities_do_not_alias_delimiters():
    facts = [TypedFact(TYPE, ["n", "A:B"]), TypedFact(TYPE, ["n", "A"])]
    suite = StructuralSuite(TYPE, closed_classes=["A:B", "A"], required=[
        RequiredField("A:B", "C"), RequiredField("A", "B:C")])
    report = validate_structural(facts, suite)
    assert len({v.sources for v in report.violations}) == 2


def test_report_ties_have_a_total_order():
    fact = TypedFact(TYPE, ["n", "C"])
    first = StructuralViolation("missing-required", (fact,), ("a",), "first")
    second = StructuralViolation("missing-required", (fact,), ("b",), "second")
    third = StructuralViolation("missing-required", (fact,), ("b",), "third")
    report = StructuralReport((third, second, first), False, 1)
    assert report.violations == (first, second, third)


def test_cancellation_during_input_consumption():
    consumed = []

    def facts():
        for i in range(5000):
            consumed.append(i)
            yield TypedFact(TYPE, [f"n{i}", "C"])

    with pytest.raises(ReasoningCancelledError):
        validate_structural(facts(), StructuralSuite(TYPE, closed_classes=["C"]),
                            cancel=lambda: len(consumed) >= 10)
    assert len(consumed) < 5000


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_minimum_and_maximum_cardinality_with_duplicate_input(count):
    member = TypedFact(TYPE, ["n", "C"])
    properties = [TypedFact("p", ["n", f"v{i}"]) for i in range(count)]
    facts = [member, *properties, *properties]
    suite = StructuralSuite(TYPE, closed_classes=["C"],
                            cardinalities=[CardinalityBound("C", "p", 1, 2)])
    report = validate_structural(facts, suite)
    assert report.clean == (1 <= count <= 2)
    if not report.clean:
        assert member in report.violations[0].facts
        assert set(properties) <= set(report.violations[0].facts)


def test_bounded_reports_do_not_depend_on_constraint_order():
    facts = [TypedFact(TYPE, ["n", "C"])]
    bounds = [CardinalityBound("C", "p", 2, None), CardinalityBound("C", "p", 1, 2)]
    reports = [validate_structural(facts, StructuralSuite(
        TYPE, closed_classes=["C"], cardinalities=items), max_violations=1)
        for items in (bounds, list(reversed(bounds)))]
    assert reports[0] == reports[1]


def test_cancellation_during_constraint_checks():
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 20

    suite = StructuralSuite(TYPE, closed_classes=["C"],
                            required=[RequiredField("C", f"p{i}") for i in range(100)])
    with pytest.raises(ReasoningCancelledError):
        validate_structural([TypedFact(TYPE, ["n", "C"])], suite, cancel=cancel)


@pytest.mark.parametrize("mode", ["one-of", "at-most-one"])
@pytest.mark.parametrize("present", [(), ("a",), ("a", "b")])
def test_exclusivity_counts_predicates_not_property_facts(mode, present):
    member = TypedFact(TYPE, ["n", "C"])
    facts = [member, *(TypedFact(p, ["n", str(i)]) for p in present for i in range(2))]
    suite = StructuralSuite(TYPE, closed_classes=["C"],
                            exclusive=[ExclusiveGroup("C", ["a", "b"], mode)])
    report = validate_structural(facts, suite)
    assert report.clean == (len(present) == 1 if mode == "one-of" else len(present) <= 1)
    if not report.clean:
        assert set(report.violations[0].facts) == set(facts)


def test_report_is_identical_across_hash_seeds():
    import os
    import subprocess
    import sys

    script = """
from gestaltdb.reasoning.structural import StructuralReport, StructuralViolation
from gestaltdb.reasoning.typed import TypedFact
fact = TypedFact('type', ['n', 'C'])
print(StructuralReport(tuple(StructuralViolation('missing-required', (fact,), (s,), s)
    for s in ('c', 'a', 'b')), False, 1))
"""
    outputs = [subprocess.check_output([sys.executable, "-c", script], text=True,
                                      env={**os.environ, "PYTHONHASHSEED": str(seed)})
               for seed in (1, 2, 3)]
    assert len(set(outputs)) == 1


@pytest.mark.parametrize("kind,value", [
    ("boolean", True), ("integer", 1), ("float", 1.0), ("text", "x"),
    ("bytes", b"x"), ("instant", 1), ("null", None),
])
def test_datatype_checks_all_object_arguments_without_coercion(kind, value):
    member = TypedFact(TYPE, ["n", "C"])
    literal = Literal(kind, value)
    good = TypedFact("p", ["n", literal, literal])
    bad = TypedFact("p", ["n", literal, "entity-reference"])
    suite = StructuralSuite(TYPE, closed_classes=["C"],
                            datatypes=[DatatypeConstraint("C", "p", kind)])
    assert validate_structural([member, good], suite).clean
    report = validate_structural([member, bad], suite)
    assert len(report.violations) == 1
    assert set(report.violations[0].facts) == {member, bad}
    assert validate_structural([TypedFact(TYPE, ["n", "Open"]), bad], suite).clean
