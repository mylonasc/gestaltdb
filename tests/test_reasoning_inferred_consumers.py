"""Inferred query-to-managed-world scenario with transition parity.

A typed session answers an explicit inferred goal; the caller publishes the
answers as claims into a separate managed world (no automatic rewrite), then
drives correct/retract transitions and re-publishes. Every step compares the
session, the managed world and fresh recomputation, and old pinned readers
stay unchanged. Sampler-artifact building is explicitly out of scope.
"""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.sessions import (
    SessionScope,
    advance_session,
    start_session,
)
from gestaltdb.reasoning.catalog import CatalogEntry, RuleCatalog
from gestaltdb.reasoning.evidence_ledger import AssertionVersion, EvidenceLedger
from gestaltdb.reasoning.maintenance import rematerialize
from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
from gestaltdb.versioning import VersionOperation

MANAGED_WORLD = "inferred"


def program():
    x, y = Variable("x"), Variable("y")
    return GenericProgram([
        GenericRule("r1", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x])),
        GenericRule("r2", [TypedAtom("person", [x])], [], TypedAtom("entity", [x])),
    ])


def catalog_for(rules):
    return RuleCatalog(tuple(
        CatalogEntry(rule.rule_id, "v1", GenericProgram([rule]).digest, 1) for rule in rules.rules))


def scope():
    return SessionScope(world="default", valid_time_us=50,
                        selection=ClaimSelection(), mapping=ClaimMapping())


def start(graph, rules):
    return start_session(graph, rules, catalog_for(rules), scope=scope(), limits=ReasoningLimits())


def assert_member(graph, subject, agent, group="g1"):
    return graph.epistemic.assert_claim(subject=subject, predicate="memberOf", object=group,
                              agent=agent, polarity="positive", valid=(0, 100))


def goal_persons(session):
    return {fact.values[0] for fact in session.facts if fact.predicate == "person"}


def fresh_persons(rules, graph):
    seen = {}
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        for version in view.iter_claims_as_of(world="default"):
            if version.claim is None or version.operation not in (VersionOperation.ASSERT, VersionOperation.CORRECT):
                continue
            claim = version.claim
            seen[version.version_id] = TypedFact(claim.predicate, (claim.subject, claim.object))
    ledger = EvidenceLedger("s")
    for version_id, fact in seen.items():
        ledger = ledger.transition(scope_id="s", assertions=[AssertionVersion(version_id, fact)]).ledger
    closure = rematerialize(rules, ledger.facts, limits=ReasoningLimits())
    return {fact.values[0] for fact in closure.facts if fact.predicate == "person"}


def managed_persons(graph, through_commit=None):
    kwargs = {} if through_commit is None else {"through_commit": through_commit}
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50, **kwargs) as view:
        return {version.claim.subject for version in
                view.iter_claims_as_of(world=MANAGED_WORLD) if version.claim is not None}


def horizon(graph):
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        return view.provenance.commit_horizon


def publish_persons(graph, session):
    """Caller-driven reconcile of the managed world to the session goal."""
    horizon = session.commit_horizon
    current = managed_persons(graph)
    desired = goal_persons(session)
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50, through_commit=horizon) as view:
        by_subject = {}
        for version in view.iter_claims_as_of(world=MANAGED_WORLD):
            if version.claim is not None:
                by_subject[version.claim.subject] = version.logical_id
    for subject in sorted(desired - current):
        graph.epistemic.assert_claim(subject=subject, predicate="isPerson", object="yes",
                           agent="session-publisher", polarity="positive", valid=(0, 100),
                           provenance={"session_horizon": horizon}, world=MANAGED_WORLD)
    for subject in sorted(current - desired):
        graph.epistemic.retract_claim(by_subject[subject], valid=(0, 100))


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_query_publish_correct_retract_cycle(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        rules = program()
        one = assert_member(graph, "a", agent="one")
        assert_member(graph, "a", agent="two")
        assert_member(graph, "b", agent="one")
        session = start(graph, rules)
        assert goal_persons(session) == fresh_persons(rules, graph) == {"a", "b"}

        publish_persons(graph, session)
        horizon_after_publish = horizon(graph)
        assert managed_persons(graph) == {"a", "b"}

        # Correct one assertion: managed world is unchanged, old reader stable.
        graph.epistemic.correct_claim(one.logical_id, supersedes_version_id=one.version_id, valid=(0, 100))
        moved = advance_session(graph, session, limits=ReasoningLimits())
        assert not moved.rebuilt
        assert goal_persons(moved.session) == fresh_persons(rules, graph) == {"a", "b"}
        publish_persons(graph, moved.session)
        assert managed_persons(graph) == {"a", "b"}
        assert managed_persons(graph, through_commit=horizon_after_publish) == {"a", "b"}

        # Retract one of two independent versions: the fact survives.
        graph.epistemic.retract_claim(one.logical_id, valid=(0, 100), supersedes_version_id=one.version_id)
        moved = advance_session(graph, moved.session, limits=ReasoningLimits())
        assert not moved.rebuilt
        assert goal_persons(moved.session) == fresh_persons(rules, graph) == {"a", "b"}
        publish_persons(graph, moved.session)
        assert managed_persons(graph) == {"a", "b"}

        # Retract the final version: the fact disappears from both views.
        remaining = [v for v in graph.epistemic.iter_claim_versions()
                     if v.claim is not None and v.claim.subject == "a"
                     and v.claim.agent == "two" and v.operation == VersionOperation.ASSERT]
        assert len(remaining) == 1
        graph.epistemic.retract_claim(remaining[0].logical_id, valid=(0, 100),
                            supersedes_version_id=remaining[0].version_id)
        moved = advance_session(graph, moved.session, limits=ReasoningLimits())
        assert not moved.rebuilt
        assert goal_persons(moved.session) == fresh_persons(rules, graph) == {"b"}
        publish_persons(graph, moved.session)
        assert managed_persons(graph) == {"b"}
        assert managed_persons(graph, through_commit=horizon_after_publish) == {"a", "b"}
    finally:
        graph.close()
