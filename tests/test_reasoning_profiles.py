"""RE-23 gated profile matrix: rows, subsets, approvals and spot checks."""

import pytest

from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits, Variable
from gestaltdb.reasoning.profiles import (
    MATRIX,
    NAMED_SUBSETS,
    approval_checklist,
    check_construct,
    matrix_digest,
    subset_constructs,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


def test_matrix_is_complete_and_pinned():
    assert len(MATRIX) == len({item.name for item in MATRIX})
    assert MATRIX == tuple(sorted(MATRIX, key=lambda item: item.name))
    roles = {item.role for item in MATRIX}
    assert roles == {"entailment", "validation", "equality", "deferred"}
    assert {item.standing for item in MATRIX} == {"supported", "validation-only", "deferred"}
    for item in MATRIX:
        assert item.note and item.mechanism
        if item.standing == "deferred":
            assert item.mechanism == "none"
    assert set(NAMED_SUBSETS) == {"gestaltdb-rl-core-v1", "gestaltdb-validation-v1", "gestaltdb-equality-v1"}
    assert NAMED_SUBSETS["gestaltdb-rl-core-v1"] == frozenset(
        item.name for item in MATRIX if item.role == "entailment")
    digest = matrix_digest()
    assert digest.startswith("sha256:") and len(digest) == 71
    assert digest == matrix_digest()
    assert len(approval_checklist()) == 5


def test_gates_admit_named_subsets_only():
    assert check_construct("subclass").name == "subclass"
    assert check_construct("disjointness", "gestaltdb-validation-v1").mechanism == "reasoning.validation"
    assert check_construct("same-as", "gestaltdb-equality-v1").mechanism == "reasoning.equality"
    assert subset_constructs("gestaltdb-rl-core-v1") == tuple(sorted(NAMED_SUBSETS["gestaltdb-rl-core-v1"]))
    with pytest.raises(ReasoningError, match="disjointness.*not in subset"):
        check_construct("disjointness")
    with pytest.raises(ReasoningError, match="non-Horn"):
        check_construct("disjunctive-head", "gestaltdb-rl-core-v1")
    with pytest.raises(ReasoningError, match="unknown construct"):
        check_construct("teleportation")
    with pytest.raises(ReasoningError, match="unknown profile subset"):
        check_construct("subclass", "everything")
    with pytest.raises(ReasoningError):
        check_construct("", "gestaltdb-rl-core-v1")
    with pytest.raises(ReasoningError):
        subset_constructs("nope")


def test_model_axioms_all_have_supported_rows():
    from gestaltdb.ontology.model import Axiom

    union_members = Axiom.__args__
    assert len(union_members) == 13
    names = {item.__name__ for item in union_members}
    assert names == {"SubClassOf", "SubPropertyOf", "EquivalentClasses", "EquivalentProperties",
                     "Domain", "Range", "InverseOf", "Symmetric", "Transitive",
                     "PropertyChain", "IntersectionSubClassOf", "UnionSubClassOf", "Reflexive"}
    rows = {item.name for item in MATRIX if item.role == "entailment"}
    assert rows == {"subclass", "subproperty", "equivalent-classes", "equivalent-properties",
                    "domain", "range", "inverse", "symmetric", "transitive",
                    "property-chain", "intersection-subclass-left", "union-subclass-left",
                    "scoped-reflexivity"}


def test_supported_entailment_spot_check_with_exact_origins():
    from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
    from gestaltdb.reasoning import DatalogEngine
    from gestaltdb.reasoning.model import Fact

    assert check_construct("subproperty").mechanism == "ontology.compiler"
    plan = compile_ontology(Ontology("spot", [SubPropertyOf("narrow", "wide")]))
    result = DatalogEngine().evaluate(plan.program, [Fact("a", "narrow", "b")], limits=ReasoningLimits())
    assert ("a", "wide", "b") in {(fact.subject, fact.predicate, fact.object) for fact in result.facts}


def test_validation_and_equality_rows_match_implementations():
    from gestaltdb.reasoning.terms import Literal
    from gestaltdb.reasoning.typed import TypedFact
    from gestaltdb.reasoning.validation import ValidationSuite, validate

    assert check_construct("disjointness", "gestaltdb-validation-v1").standing == "validation-only"
    report = validate([TypedFact("type", ["n", "A"]), TypedFact("type", ["n", "B"])],
                      ValidationSuite("type", disjoint=[("A", "B")]))
    assert [violation.kind for violation in report.violations] == ["disjoint-members"]
    from gestaltdb.reasoning.equality import EqualityScope, SameAs, canonicalize

    assert check_construct("same-as", "gestaltdb-equality-v1").standing == "supported"
    mapping = canonicalize([TypedFact("p", ["a"])], [SameAs("b", "a")], scope=EqualityScope("s"))
    assert mapping.representative("b") == "a"


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
from standalone.profiles import MATRIX, check_construct, matrix_digest, subset_constructs
assert len(MATRIX) == len(set(item.name for item in MATRIX))
assert check_construct('transitive').mechanism == 'ontology.compiler'
assert 'subclass' in subset_constructs('gestaltdb-rl-core-v1')
assert matrix_digest() == matrix_digest()
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
