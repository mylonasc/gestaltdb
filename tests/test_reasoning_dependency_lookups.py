"""Dependency lookups reuse immutable generation-local maps."""

import pickle

import pytest

from gestaltdb.reasoning.maintenance import DependencyIndex, Support
from gestaltdb.reasoning.typed import TypedFact


def test_lookup_results_are_cached_complete_and_read_only():
    left, right = TypedFact("p", ["a"]), TypedFact("p", ["b"])
    conclusion = TypedFact("q", ["a"])
    first = Support(conclusion, "r1", [left])
    second = Support(conclusion, "r2", [left, right])
    index = DependencyIndex("program", "horizon", [first, second, first])
    clone = DependencyIndex("program", "horizon", [second, first])
    assert index == clone and index.digest == clone.digest
    assert set(index.supports_for(conclusion)) == {first, second}
    assert index.supports_for(conclusion) is index.supports_for(conclusion)
    assert index.derived_from(left) == (conclusion,)
    assert index.derived_from(left) is index.derived_from(left)
    assert set(index.premises_of(conclusion)) == {left, right}
    assert index.premises_of(conclusion) is index.premises_of(conclusion)
    assert index.supports_for(TypedFact("missing", ["a"])) == ()
    with pytest.raises(TypeError):
        index._supports_by_fact[conclusion] = ()


def test_indexes_do_not_retain_the_mutable_constructor_list():
    premise, conclusion = TypedFact("p", ["a"]), TypedFact("q", ["a"])
    items = [Support(conclusion, "r", [premise])]
    index = DependencyIndex("program", "horizon", items)
    items.clear()
    assert len(index.supports_for(conclusion)) == 1
    assert index.derived_from(premise) == (conclusion,)


def test_derived_read_only_maps_are_rebuilt_on_pickle_roundtrip():
    premise, conclusion = TypedFact("p", ["a"]), TypedFact("q", ["a"])
    index = DependencyIndex("program", "horizon", [Support(conclusion, "r", [premise])])
    restored = pickle.loads(pickle.dumps(index))
    assert restored == index and restored.digest == index.digest
    assert restored.derived_from(premise) == (conclusion,)
    with pytest.raises(TypeError):
        restored._supports_by_fact[conclusion] = ()
