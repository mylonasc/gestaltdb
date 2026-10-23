"""Session advance over real commit deltas with fresh-recomputation parity."""

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


def start(graph, rules=None):
    rules = rules or program()
    return start_session(graph, rules, catalog_for(rules), scope=scope(), limits=ReasoningLimits())


def assert_member(graph, subject, group="g1", agent="registry"):
    return graph.assert_claim(subject=subject, predicate="memberOf", object=group,
                              agent=agent, polarity="positive", valid=(0, 100))


def fresh_facts(rules, graph):
    seen = {}
    with graph.read_view(valid_time=50) as view:
        for version in view.iter_claims_as_of(world="default"):
            if version.claim is None or version.operation not in (VersionOperation.ASSERT, VersionOperation.CORRECT):
                continue
            claim = version.claim
            seen[version.version_id] = TypedFact(claim.predicate, (claim.subject, claim.object))
    ledger = EvidenceLedger("s")
    for version_id, fact in seen.items():
        ledger = ledger.transition(scope_id="s", assertions=[AssertionVersion(version_id, fact)]).ledger
    return set(rematerialize(rules, ledger.facts, limits=ReasoningLimits()).facts)


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_noop_advance_insert_retract_correct(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = assert_member(graph, "a")
        session = start(graph)
        assert set(session.facts) == fresh_facts(session.program, graph)
        assert len(session.facts) == 3  # memberOf + person + entity

        noop = advance_session(graph, session, limits=ReasoningLimits())
        assert noop.session is session and not noop.rebuilt
        assert noop.inserted == noop.retracted == ()

        assert_member(graph, "b")
        moved = advance_session(graph, session, limits=ReasoningLimits())
        assert not moved.rebuilt
        assert {f.values[0] for f in moved.inserted} == {"b"}
        assert set(moved.session.facts) == fresh_facts(moved.session.program, graph)

        graph.retract_claim(first.logical_id, valid=(0, 100))
        after_retract = advance_session(graph, moved.session, limits=ReasoningLimits())
        assert not after_retract.rebuilt
        assert {f.values[0] for f in after_retract.retracted} >= {"a"}
        assert set(after_retract.session.facts) == fresh_facts(after_retract.session.program, graph)

        target = assert_member(graph, "c")
        corrected = graph.correct_claim(target.logical_id, supersedes_version_id=target.version_id,
                                        valid=(0, 100))
        assert corrected.supersedes_version_id == target.version_id
        after_correct = advance_session(graph, after_retract.session, limits=ReasoningLimits())
        assert not after_correct.rebuilt
        # The assert(c)+correct(c) commits arrive in one advance: the base
        # fact is new to the session even though the correction itself is
        # logically neutral.
        assert after_correct.inserted == (TypedFact("memberOf", ("c", "g1")),)
        assert after_correct.retracted == ()
        assert set(after_correct.session.facts) == fresh_facts(after_correct.session.program, graph)
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_alternative_evidence_survives_single_retraction(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        one = assert_member(graph, "a", agent="one")
        assert_member(graph, "a", agent="two")
        session = start(graph)
        graph.retract_claim(one.logical_id, valid=(0, 100), supersedes_version_id=one.version_id)
        moved = advance_session(graph, session, limits=ReasoningLimits())
        assert not moved.rebuilt
        assert TypedFact("person", ("a",)) in set(moved.session.facts)
        assert set(moved.session.facts) == fresh_facts(moved.session.program, graph)
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_stale_supersedes_retraction_applies_logically(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        first = assert_member(graph, "a")
        # v1 is retired before the session starts; a later retraction naming
        # v1 still retracts the logical claim the session observes as v2.
        graph.correct_claim(first.logical_id, supersedes_version_id=first.version_id, valid=(0, 100))
        session = start(graph)
        graph.retract_claim(first.logical_id, valid=(0, 100), supersedes_version_id=first.version_id)
        moved = advance_session(graph, session, limits=ReasoningLimits())
        assert not moved.rebuilt
        # The retraction wins over the whole logical claim in
        # retract-winner resolution, so the fact disappears entirely.
        assert TypedFact("person", ("a",)) not in set(moved.session.facts)
        assert set(moved.session.facts) == fresh_facts(moved.session.program, graph) == set()
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_overwide_gap_rebuilds_explicitly(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        assert_member(graph, "a")
        session = start(graph)
        assert_member(graph, "b")
        assert_member(graph, "c")
        moved = advance_session(graph, session, limits=ReasoningLimits(), max_advance_commits=1)
        assert moved.rebuilt
        assert set(moved.session.facts) == fresh_facts(moved.session.program, graph)
        assert TypedFact("person", ("c",)) in set(moved.session.facts)
    finally:
        graph.close()


def test_session_rejects_uncovered_rules_and_foreign_database(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    other = GraphDB.create(tmp_path / "other", backend="lmdb", serializer="json")
    try:
        assert_member(graph, "a")
        rules = program()
        partial = RuleCatalog((CatalogEntry("r1", "v1", GenericProgram([rules.rules[0]]).digest, 1),))
        with pytest.raises(Exception, match="catalog does not cover"):
            start_session(graph, rules, partial, scope=scope(), limits=ReasoningLimits())
        session = start(graph, rules)
        with pytest.raises(Exception, match="different database"):
            advance_session(other, session, limits=ReasoningLimits())
    finally:
        graph.close()
        other.close()
