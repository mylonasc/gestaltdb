"""RE-11 snapshot-local equality, canonical representatives and safe rebuilds."""

import pytest

from gestaltdb.reasoning.equality import (
    CanonicalMapping,
    DifferentFrom,
    EqualityScope,
    FunctionalProperty,
    InequalityConflict,
    SameAs,
    canonicalize,
)
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import TypedFact


SCOPE = EqualityScope("pinned-horizon-1")


def test_links_validate_and_canonicalize_deterministically():
    assert SameAs("b", "a") == SameAs("a", "b")
    assert DifferentFrom("b", "a") == DifferentFrom("a", "b")
    with pytest.raises(ReasoningError):
        SameAs("a", "a")
    with pytest.raises(ReasoningError):
        DifferentFrom("", "a")
    with pytest.raises(ReasoningError):
        FunctionalProperty("")
    facts = [TypedFact("knows", ["a", "b"]), TypedFact("knows", ["b", "c"])]
    first = canonicalize(facts, [SameAs("a", "b"), SameAs("b", "c")], scope=SCOPE)
    assert isinstance(first, CanonicalMapping)
    assert first.representative("c") == "a"
    assert first.members("a") == ("a", "b", "c")
    assert first.representative("zzz") == "zzz"
    assert first.members("zzz") == ("zzz",)
    assert first.justification("a") == (SameAs("a", "b"), SameAs("b", "c"))
    assert first.justification("zzz") == ()
    assert not first.conflicts
    assert first == canonicalize(list(reversed(facts)), [SameAs("b", "c"), SameAs("a", "b")], scope=SCOPE)
    assert first.digest == canonicalize(facts, [SameAs("a", "b"), SameAs("b", "c")], scope=SCOPE).digest
    assert first.digest != canonicalize(facts, [SameAs("a", "b")], scope=SCOPE).digest


def test_removal_splits_classes_and_conflicts_carry_evidence():
    facts = [TypedFact("p", ["a"]), TypedFact("p", ["b"]), TypedFact("p", ["c"])]
    links = [SameAs("a", "b"), SameAs("b", "c"), DifferentFrom("a", "c")]
    run = canonicalize(facts, links, scope=SCOPE)
    assert run.representative("c") == "a"
    assert len(run.conflicts) == 1
    conflict = run.conflicts[0]
    assert isinstance(conflict, InequalityConflict)
    assert (conflict.left, conflict.right, conflict.representative) == ("a", "c", "a")
    assert set(conflict.evidence) == {SameAs("a", "b"), SameAs("b", "c"), DifferentFrom("a", "c")}
    assert conflict in run.conflicts and run.digest
    # Removing the bridge link rebuilds two classes; the conflict disappears.
    rebuilt = canonicalize(facts, [SameAs("a", "b"), DifferentFrom("a", "c")], scope=SCOPE)
    assert rebuilt.representative("c") == "c"
    assert rebuilt.representative("b") == "a"
    assert not rebuilt.conflicts


def test_functional_properties_generate_links_without_merging_silently():
    facts = [TypedFact("mbox", ["n", "a@x"]), TypedFact("mbox", ["n", "b@x"]),
             TypedFact("mbox", ["m", "c@x"])]
    run = canonicalize(facts, [FunctionalProperty("mbox")], scope=SCOPE)
    assert run.representative("b@x") == "a@x"
    assert set(run.justification("a@x")) == {SameAs("a@x", "b@x")}
    assert run.representative("m") == "m"
    rewritten = run.rewrite(facts)
    assert TypedFact("mbox", ["n", "a@x"]) in rewritten
    assert all(isinstance(value, str) for fact in rewritten for value in fact.values)
    literals = [TypedFact("age", ["n", Literal("integer", 30)]),
                TypedFact("age", ["n", Literal("integer", 31)])]
    literal_run = canonicalize(literals, [FunctionalProperty("age")], scope=SCOPE)
    assert literal_run.representative("n") == "n"
    assert TypedFact("age", ["n", Literal("integer", 30)]) in literal_run.rewrite(literals)


def test_scope_isolation_bounds_and_cancellation():
    facts = [TypedFact("p", ["a"]), TypedFact("p", ["b"])]
    left = canonicalize(facts, [SameAs("a", "b")], scope=EqualityScope("h1"))
    right = canonicalize(facts, [SameAs("a", "b")], scope=EqualityScope("h2"))
    assert left.digest != right.digest
    assert left.representative("b") == right.representative("b") == "a"
    assert left == canonicalize(facts, [SameAs("a", "b")], scope=EqualityScope("h1"))
    with pytest.raises(ReasoningError):
        canonicalize(facts, [SameAs("a", "b")], scope="not-a-scope")
    with pytest.raises(ReasoningError):
        canonicalize(["not-a-fact"], [], scope=SCOPE)
    with pytest.raises(ReasoningError):
        canonicalize(facts, ["not-a-link"], scope=SCOPE)
    with pytest.raises(ReasoningError):
        canonicalize(facts, [], scope=SCOPE, max_entities=True)
    with pytest.raises(ReasoningLimitError, match="max_entities"):
        canonicalize([TypedFact("p", [f"n{i}"]) for i in range(10)], [], scope=SCOPE, max_entities=5)
    with pytest.raises(ReasoningCancelledError):
        canonicalize(facts, [SameAs("a", "b")] * 2000, scope=SCOPE, cancel=lambda: True)


def test_no_unique_name_assumption_and_literal_exclusion():
    run = canonicalize([TypedFact("p", ["a"]), TypedFact("p", ["b"])], [], scope=SCOPE)
    assert run.representative("a") == "a" and run.representative("b") == "b"
    assert run.members("a") == ("a",)
    mixed = canonicalize([TypedFact("p", [Literal("integer", 1)]), TypedFact("p", ["1"])],
                         [], scope=SCOPE)
    assert mixed.representative("1") == "1"
    assert run == canonicalize([TypedFact("p", ["a"]), TypedFact("p", ["b"])], [], scope=SCOPE)


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
from standalone.equality import EqualityScope, SameAs, canonicalize
from standalone.typed import TypedFact
mapping = canonicalize([TypedFact('p', ['a'])], [SameAs('b', 'a')], scope=EqualityScope('h'))
assert mapping.representative('b') == 'a' and not mapping.conflicts
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
