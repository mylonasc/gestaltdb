"""RE-24 bounded EL feasibility: completion, proof checking and oracle parity."""

import random

import pytest

from gestaltdb.reasoning.el import (
    EL_PROFILE,
    ELClassification,
    ELDerivation,
    check_proof,
    classify,
    holds_in_all_models,
    normalize_axiom,
)
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError


def test_profile_gate_and_normalization():
    assert EL_PROFILE == "el-experimental-v0"
    assert normalize_axiom(("sub", "A", "B"), profile=EL_PROFILE).canonical() == ("sub", "A", "B")
    assert normalize_axiom(("and", ("B", "A", "A"), "C"), profile=EL_PROFILE).canonical() == (
        ("and", ("A", "B"), "C"))
    assert normalize_axiom(("and", ("A",), "C"), profile=EL_PROFILE).canonical() == ("sub", "A", "C")
    with pytest.raises(ReasoningError, match="named terms only"):
        normalize_axiom(("some-right", "A", "r", ("and", ("B",), "C")), profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="name every conjunct"):
        normalize_axiom(("and", ("A", ("sub", "B", "C")), "D"), profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="name every conjunct"):
        normalize_axiom(("and", (), "D"), profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="unsupported EL axiom form"):
        normalize_axiom(("union-right", "A", "B"), profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="non-empty tuple"):
        normalize_axiom([], profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="experimental EL profile"):
        normalize_axiom(("sub", "A", "B"), profile="owl-dl")
    with pytest.raises(ReasoningError, match="experimental EL profile"):
        classify([], profile="el-full")
    assert normalize_axiom(("some-left", "r", "B", "C"), profile=EL_PROFILE).canonical() == (
        "some-left", "r", "B", "C")


def test_completion_rules_with_witness_identity():
    specs = [("sub", "Cat", "Mammal"), ("sub", "Mammal", "Animal"),
             ("and", ("Pet", "Mammal"), "HousePet"),
             ("sub", "Felix", "Cat"), ("sub", "Felix", "Pet"),
             ("some-right", "HousePet", "hasParent", "HousePet"),
             ("some-left", "hasParent", "HousePet", "Loved")]
    out = classify(specs, profile=EL_PROFILE)
    assert isinstance(out, ELClassification)
    assert out.holds("Cat", "Animal") and out.holds("Felix", "HousePet")
    assert out.holds("Felix", "Loved")
    assert not out.holds("Animal", "Cat")
    assert out.holds("Cat", "Cat")
    assert all(item.round >= 1 for item in out.derivations)
    assert len({item.digest for item in out.derivations}) == len(out.derivations)
    assert out == classify(list(reversed(specs)), profile=EL_PROFILE)
    assert out.witnesses("Felix", "Loved")[0].rule == "existential-left"
    assert check_proof(specs, out, profile=EL_PROFILE) == len(out.derivations)


def test_proof_premises_are_older_and_checkable():
    specs = [("sub", "A", "B"), ("sub", "B", "C"), ("sub", "C", "D")]
    out = classify(specs, profile=EL_PROFILE)
    assert out.holds("A", "D")
    order = {}
    for index, derivation in enumerate(out.derivations):
        conclusions = (derivation.conclusion,)
        for conclusion in conclusions:
            order.setdefault(conclusion, index)
    for derivation in out.derivations:
        for premise in derivation.premises:
            if premise[0] == "sub":
                key = ("sub", premise[1], premise[2])
                if key in order:
                    assert order[key] < order[derivation.conclusion]
    assert check_proof(specs, out, profile=EL_PROFILE) == len(out.derivations)
    with pytest.raises(ReasoningError):
        check_proof([("sub", "A", "B")], out, profile=EL_PROFILE)
    with pytest.raises(ReasoningError, match="experimental EL profile"):
        check_proof(specs, out, profile="nope")
    with pytest.raises(ReasoningError):
        check_proof(specs, "nope", profile=EL_PROFILE)


def test_termination_budgets_and_cancellation():
    chain = [("sub", f"C{i}", f"C{i+1}") for i in range(30)]
    out = classify(chain, profile=EL_PROFILE)
    assert out.holds("C0", "C30")
    assert out.rounds <= 31
    with pytest.raises(ReasoningLimitError, match="max_subsumptions"):
        classify(chain, profile=EL_PROFILE, max_subsumptions=10)
    with pytest.raises(ReasoningLimitError, match="max_rounds"):
        classify(chain, profile=EL_PROFILE, max_rounds=1)
    with pytest.raises(ReasoningError, match="positive integer"):
        classify(chain, profile=EL_PROFILE, max_rounds=True)
    with pytest.raises(ReasoningCancelledError):
        classify(chain, profile=EL_PROFILE, cancel=lambda: True)
    with pytest.raises(ReasoningError):
        classify("nope", profile=EL_PROFILE)


def test_oracle_agrees_with_completion_on_random_small_ontologies():
    rng = random.Random(20261024)
    concepts = ["A", "B", "C"]
    roles = ["r"]
    checked = 0
    for case in range(40):
        specs = []
        for _ in range(rng.randint(1, 4)):
            kind = rng.choice(["sub", "and", "some-right", "some-left"])
            if kind == "sub":
                specs.append(("sub", rng.choice(concepts), rng.choice(concepts)))
            elif kind == "and":
                specs.append(("and", tuple(rng.sample(concepts, 2)), rng.choice(concepts)))
            elif kind == "some-right":
                specs.append(("some-right", rng.choice(concepts), rng.choice(roles), rng.choice(concepts)))
            else:
                specs.append(("some-left", rng.choice(roles), rng.choice(concepts), rng.choice(concepts)))
        out = classify(specs, profile=EL_PROFILE)
        assert check_proof(specs, out, profile=EL_PROFILE) == len(out.derivations)
        for sub in concepts:
            for sup in concepts:
                oracle = holds_in_all_models(specs, sub, sup, profile=EL_PROFILE, domain_size=2)
                if oracle is not None:
                    assert out.holds(sub, sup) == oracle, (case, specs, sub, sup)
                    checked += 1
    assert checked > 100


def test_oracle_rejects_vacuous_and_bad_inputs():
    assert holds_in_all_models([], "A", "A", profile=EL_PROFILE, domain_size=1) is True
    assert holds_in_all_models([], "A", "B", profile=EL_PROFILE, domain_size=1) is False
    assert holds_in_all_models([("sub", "A", "B")], "A", "B", profile=EL_PROFILE, domain_size=2) is True
    with pytest.raises(ReasoningError, match="domain_size"):
        holds_in_all_models([], "A", "B", profile=EL_PROFILE, domain_size=4)
    with pytest.raises(ReasoningError, match="experimental EL profile"):
        holds_in_all_models([], "A", "B", profile="x")
    with pytest.raises(ReasoningError):
        holds_in_all_models("nope", "A", "B", profile=EL_PROFILE)


def test_derivation_validation_rejects_malformed_witnesses():
    with pytest.raises(ReasoningError):
        ELDerivation("magic-rule", ("sub", "A", "B"), [(("sub", "A", "B"))], 1)
    with pytest.raises(ReasoningError):
        ELDerivation("transitivity", ("sub", "A"), [(("sub", "A", "B"))], 1)
    with pytest.raises(ReasoningError):
        ELDerivation("transitivity", ("sub", "A", "B"), [], 1)
    with pytest.raises(ReasoningError):
        ELDerivation("transitivity", ("sub", "A", "B"), [(("nope", 1))], 1)
    with pytest.raises(ReasoningError):
        ELDerivation("transitivity", ("sub", "A", "B"), [(("sub", "A", "B"))], 0)
    assert ELDerivation("transitivity", ("sub", "A", "C"),
                        [(("sub", "A", "B")), (("sub", "B", "C"))], 2).digest


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
from standalone.el import EL_PROFILE, check_proof, classify, holds_in_all_models
specs = [('sub', 'A', 'B'), ('sub', 'B', 'C')]
out = classify(specs, profile=EL_PROFILE)
assert out.holds('A', 'C') and check_proof(specs, out, profile=EL_PROFILE) == len(out.derivations)
assert holds_in_all_models(specs, 'A', 'C', profile=EL_PROFILE, domain_size=2) is True
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
