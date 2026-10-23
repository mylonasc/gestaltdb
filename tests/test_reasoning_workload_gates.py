"""RE-18 workload gates: crash boundaries, stale plans, backend-gated recovery, parity."""

import pytest

from gestaltdb.reasoning.catalog import CatalogEntry, RuleCatalog, pin_generation
from gestaltdb.reasoning.maintenance import (
    propagate_insertions,
    rematerialize,
    retract_facts,
)
from gestaltdb.reasoning.model import (
    ReasoningCancelledError,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
)
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact


X, Y, Z = Variable("x"), Variable("y"), Variable("z")


def chain_program(extra=False):
    rules = [
        GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
        GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                    TypedAtom("reach", [X, Z])),
    ]
    if extra:
        rules.append(GenericRule("loop", [TypedAtom("edge", [X, Y])], [], TypedAtom("loop", [X, Y])))
    return GenericProgram(rules)


def chain(n, tag):
    return [TypedFact("edge", [f"{tag}{i}", f"{tag}{i+1}"]) for i in range(n)]


def test_cancel_at_every_boundary_leaves_no_partial_results():
    prog = chain_program()
    base = chain(8, "a")
    material = rematerialize(prog, base, limits=ReasoningLimits())
    before_facts = list(material.facts)
    before_digest = material.index.digest
    operations = [
        lambda hook: propagate_insertions(prog, list(before_facts), [TypedFact("edge", ["x", "y"])],
                                          limits=ReasoningLimits(), index=material.index, cancel=hook),
        lambda hook: retract_facts(prog, list(before_facts), [TypedFact("edge", ["a0", "a1"])],
                                   limits=ReasoningLimits(), index=material.index, cancel=hook),
        lambda hook: rematerialize(prog, base, limits=ReasoningLimits(), cancel=hook),
    ]
    for operation in operations:
        calls = {"count": 0}

        def counting():
            calls["count"] += 1
            return False

        operation(counting)
        total = calls["count"]
        assert total >= 2
        for target in range(1, total + 1):
            calls["count"] = 0

            def boundary(target=target):
                calls["count"] += 1
                return calls["count"] >= target

            with pytest.raises(ReasoningCancelledError):
                operation(boundary)
    assert list(material.facts) == before_facts and material.index.digest == before_digest


def test_stale_plan_and_generation_rejected_at_boundaries():
    prog = chain_program()
    base = chain(6, "a")
    material = rematerialize(prog, base, limits=ReasoningLimits())
    catalog = RuleCatalog.from_dict({"entries": [{"key": "reach", "digest": "sha256:" + "ab" * 32}]})
    pinned = pin_generation("gen-1", catalog, prog.digest, material.index.horizon)
    pinned.verify(catalog, material.index.horizon)
    # Stale maintenance index is rejected before any work happens.
    with pytest.raises(ReasoningError, match="rematerialize"):
        retract_facts(chain_program(extra=True), material.facts, [TypedFact("edge", ["a0", "a1"])],
                      limits=ReasoningLimits(), index=material.index)
    # Stale catalog binding is rejected at the generation boundary.
    moved = catalog.with_entry(CatalogEntry("reach", "v2", "sha256:" + "cd" * 32, 2))
    with pytest.raises(ReasoningError, match="drifted"):
        pinned.verify(moved, material.index.horizon)
    # A generation pinned to one horizon never validates another.
    with pytest.raises(ReasoningError, match="horizon moved"):
        pinned.verify(catalog, material.index.horizon + "x")


def test_redundant_cycles_are_noops_with_stable_index():
    prog = chain_program()
    base = chain(6, "a")
    material = rematerialize(prog, base, limits=ReasoningLimits())
    digest = material.index.digest
    grown = propagate_insertions(prog, list(material.facts), list(base),
                                 limits=ReasoningLimits(), index=material.index)
    assert grown.added == () and set(grown.facts) == set(material.facts)
    assert grown.index.digest == digest
    shrunk = retract_facts(prog, list(material.facts), [TypedFact("edge", ["ghost"])],
                           limits=ReasoningLimits(), index=material.index)
    assert shrunk.removed == () and set(shrunk.facts) == set(material.facts)
    assert shrunk.overdeleted == 0 and shrunk.rederived == 0
    assert shrunk.index.digest == digest


def test_incremental_matches_rebuild_with_diagnostics():
    prog = chain_program()
    base = chain(10, "a") + chain(10, "b")
    material = rematerialize(prog, base, limits=ReasoningLimits())
    victim = TypedFact("edge", ["a0", "a1"])
    out = retract_facts(prog, material.facts, [victim], limits=ReasoningLimits(), index=material.index)
    rebuilt = rematerialize(prog, [fact for fact in base if fact != victim], limits=ReasoningLimits())
    assert set(out.facts) == set(rebuilt.facts)
    assert out.work < rebuilt.work  # diagnostics: incremental work below rebuild work
    grown = propagate_insertions(prog, list(out.facts), [victim],
                                 limits=ReasoningLimits(), index=out.index)
    assert set(grown.facts) == set(material.facts)


def test_rule_revision_rematerializes_under_new_digest():
    prog = chain_program()
    base = chain(6, "a")
    material = rematerialize(prog, base, limits=ReasoningLimits())
    revised = chain_program(extra=True)
    assert revised.digest != prog.digest
    with pytest.raises(ReasoningError, match="rematerialize"):
        propagate_insertions(revised, list(material.facts), [TypedFact("edge", ["z", "w"])],
                             limits=ReasoningLimits(), index=material.index)
    fresh = rematerialize(revised, base, limits=ReasoningLimits())
    assert TypedFact("loop", ["a0", "a1"]) in fresh.facts
    assert set(fresh.facts) >= set(material.facts)


@pytest.mark.parametrize("backend", ["lmdb", "plyvel", "pyrex"])
def test_backend_orphan_audit_and_consent_reclaim(tmp_path, backend):
    pytest.importorskip(backend)
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
    from gestaltdb.reasoning.adapters.publication import (
        audit_publication_orphans,
        reclaim_publication_orphans,
    )
    from gestaltdb.serializers import PickleSerializer

    stores = {"lmdb": LMDBStore, "plyvel": LevelDBStore, "pyrex": PyRexStore}
    graph = GraphDB(stores[backend](path=str(tmp_path / "backend")), PickleSerializer())
    try:
        report = audit_publication_orphans(graph)
        assert report.artifacts == ()
        reclaimed = reclaim_publication_orphans(graph, through_commit=0, consent=True)
        assert reclaimed.reclaimed_commit_ids == ()
        assert reclaimed.reservation_gap_ids == ()
        assert reclaimed.through_commit == 0
        with pytest.raises(ReasoningError, match="consent"):
            reclaim_publication_orphans(graph, through_commit=0, consent=False)
    finally:
        graph.close()
