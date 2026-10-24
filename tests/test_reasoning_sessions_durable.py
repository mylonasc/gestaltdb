"""Durable session persistence: atomic files, digest binding, CAS and resume parity."""

import json

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.session_store import (
    advance_persisted_session,
    load_session,
    save_session,
    session_from_dict,
    session_to_dict,
)
from gestaltdb.reasoning.adapters.sessions import SessionScope, advance_session, start_session
from gestaltdb.reasoning.catalog import CatalogEntry, RuleCatalog
from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import (
    AggregateRule,
    Aggregation,
    Comparison,
    GenericProgram,
    GenericRule,
    TypedAtom,
    TypedFact,
)
from gestaltdb.reasoning.terms import Literal
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
    from gestaltdb.reasoning.evidence_ledger import EvidenceLedger, AssertionVersion
    from gestaltdb.reasoning.maintenance import rematerialize

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
def test_roundtrip_and_resume_parity(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        assert_member(graph, "a")
        session = start(graph)
        path = tmp_path / "session.json"
        digest = save_session(session, path)
        assert digest.startswith("sha256:")
        # Simulate a process restart: drop the object, reload from disk.
        reloaded = load_session(path)
        assert reloaded.commit_horizon == session.commit_horizon
        assert set(reloaded.facts) == set(session.facts)
        assert reloaded.ledger.versions == session.ledger.versions
        assert reloaded.version_logical == session.version_logical
        assert reloaded.program.digest == session.program.digest
        assert reloaded.catalog.digest == session.catalog.digest
        assert reloaded.index.digest == session.index.digest

        assert_member(graph, "b")
        outcome = advance_persisted_session(graph, path, limits=ReasoningLimits())
        assert not outcome.rebuilt
        assert set(outcome.session.facts) == fresh_facts(outcome.session.program, graph)
        persisted = load_session(path)
        assert persisted.commit_horizon == outcome.session.commit_horizon
        assert set(persisted.facts) == set(outcome.session.facts)

        # Retraction flows through the persisted path with parity.
        target = assert_member(graph, "c")
        advance_persisted_session(graph, path, limits=ReasoningLimits())
        graph.retract_claim(target.logical_id, valid=(0, 100))
        after = advance_persisted_session(graph, path, limits=ReasoningLimits())
        assert TypedFact("person", ("c",)) not in set(after.session.facts)
        assert set(after.session.facts) == fresh_facts(after.session.program, graph)
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
@pytest.mark.parametrize("interval", [(0, 50), (50, 51), (51, 100)])
def test_reopened_persisted_session_respects_retraction_validity(tmp_path, backend, module, interval):
    pytest.importorskip(module)
    database_path = tmp_path / backend
    session_path = tmp_path / "session.json"
    graph = GraphDB.create(database_path, backend=backend, serializer="json")
    try:
        first = assert_member(graph, "a")
        save_session(start(graph), session_path)
    finally:
        graph.close()
    graph = GraphDB.open(database_path)
    try:
        graph.retract_claim(first.logical_id, valid=interval, supersedes_version_id=first.version_id)
        moved = advance_persisted_session(graph, session_path, limits=ReasoningLimits())
        fresh = start(graph)
        reloaded = load_session(session_path)
        assert not moved.rebuilt
        assert moved.session.facts == reloaded.facts == fresh.facts
        assert moved.session.version_logical == reloaded.version_logical == fresh.version_logical
        assert reloaded.commit_horizon == fresh.commit_horizon
        assert set(reloaded.facts) == fresh_facts(reloaded.program, graph)
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_overwide_gap_rebuild_persists(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        assert_member(graph, "a")
        path = tmp_path / "session.json"
        save_session(start(graph), path)
        assert_member(graph, "b")
        assert_member(graph, "c")
        outcome = advance_persisted_session(
            graph, path, limits=ReasoningLimits(), max_advance_commits=1)
        assert outcome.rebuilt
        assert set(outcome.session.facts) == fresh_facts(outcome.session.program, graph)
        assert set(load_session(path).facts) == set(outcome.session.facts)
    finally:
        graph.close()


def test_cas_and_expected_horizon(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        assert_member(graph, "a")
        path = tmp_path / "session.json"
        session = start(graph)
        save_session(session, path)
        with pytest.raises(Exception, match="changed underneath|expected_horizon"):
            save_session(session, path, expected_horizon=session.commit_horizon + 100)
        with pytest.raises(Exception, match="expected_horizon"):
            advance_persisted_session(graph, path, limits=ReasoningLimits(), expected_horizon=9999)
        # Concurrent change between load and persist fails closed.
        assert_member(graph, "b")
        advance_persisted_session(graph, path, limits=ReasoningLimits())
        stale = load_session(path)
        assert_member(graph, "c")
        advance_persisted_session(graph, path, limits=ReasoningLimits())
        with pytest.raises(Exception, match="changed underneath|reload and retry"):
            save_session(stale, path, expected_horizon=stale.commit_horizon)
    finally:
        graph.close()


def test_corrupt_and_drift_rejected(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        assert_member(graph, "a")
        path = tmp_path / "session.json"
        save_session(start(graph), path)
        raw = json.loads(path.read_text())
        tampered = dict(raw)
        tampered["commit_horizon"] = raw["commit_horizon"] + 5
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(tampered))
        with pytest.raises(Exception, match="digest mismatch"):
            load_session(bad)
        truncated = tmp_path / "truncated.json"
        truncated.write_bytes(path.read_bytes()[:100])
        with pytest.raises(Exception, match="cannot decode|digest|mapping|format"):
            load_session(truncated)
        unknown = dict(raw)
        unknown["format_version"] = 999
        unknown_path = tmp_path / "unknown.json"
        unknown_path.write_text(json.dumps(unknown))
        with pytest.raises(Exception, match="unsupported session format"):
            load_session(unknown_path)
        drifted = session_to_dict(start(graph))
        import hashlib as _hashlib

        drifted["program"]["rules"][0]["rule_id"] = "tampered"
        canonical = json.dumps(
            {k: v for k, v in drifted.items() if k != "session_digest"},
            ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode()
        drifted["session_digest"] = "sha256:" + _hashlib.sha256(canonical).hexdigest()
        with pytest.raises(Exception, match="program digest drifted"):
            session_from_dict(drifted)
    finally:
        graph.close()


def test_foreign_database_and_backwards_horizon_rejected(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    other = GraphDB.create(tmp_path / "other", backend="lmdb", serializer="json")
    try:
        assert_member(graph, "a")
        path = tmp_path / "session.json"
        save_session(start(graph), path)
        with pytest.raises(Exception, match="different database"):
            advance_persisted_session(other, path, limits=ReasoningLimits())
    finally:
        graph.close()
        other.close()


def test_literal_and_aggregate_codecs_roundtrip():
    x = Variable("x")
    lit = Literal.from_lexical("integer", "3")
    prog = GenericProgram(
        [GenericRule("r1", [TypedAtom("p", [x])],
                     [Comparison(">", x, Literal.from_lexical("integer", "1"))],
                     TypedAtom("q", [x]))],
        (AggregateRule("a1", (TypedAtom("q", [x]),), (),
                       (Aggregation("count", x, Variable("n")),),
                       TypedAtom("total", [Variable("n")])),),
    )
    assert session_from_dict is not None
    assert prog.digest.startswith("sha256:")
    assert TypedFact("q", (lit,)).values[0] == lit
