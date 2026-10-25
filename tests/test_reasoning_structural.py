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
    assert report.violations[0].sources == ("required:Trade:tradeId",)
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
    assert by_kind["cardinality-violation"].sources == ("cardinality:Trade:leg",)
    assert by_kind["datatype-violation"].sources == ("datatype:Trade:price:text",)
    assert by_kind["exclusive-violation"].sources == ("exclusive:Trade:at-most-one",)
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
