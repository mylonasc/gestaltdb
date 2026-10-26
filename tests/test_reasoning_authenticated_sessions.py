"""Durable bytes, source admission and caller compatibility remain distinct."""

from dataclasses import asdict, replace
import json
import subprocess
import sys
from pathlib import Path
import re

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.authenticated_sessions import load_authenticated_session, advance_authenticated_session
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.session_generation import SessionAdmissionLimits, bind_session_generation, catalog_for_typed_program
from gestaltdb.reasoning.adapters.session_store import save_session, load_session, session_to_dict, _digest
from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.evidence_ledger import EvidenceLedger
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact

X, Y = Variable("x"), Variable("y")
PROGRAM = GenericProgram([GenericRule("r", [TypedAtom("memberOf", [X, Y])], [], TypedAtom("person", [X]))])


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    for agent in ("one", "two"):
        graph.epistemic.assert_claim(subject="a", predicate="memberOf", object="g1", agent=agent, polarity="positive", valid=(0, 100))
    session = start_session(graph, PROGRAM, catalog_for_typed_program(PROGRAM),
        scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
    binding = bind_session_generation(graph, session)
    path = tmp_path / "session.json"
    assert save_session(session, path) == binding.session_digest
    yield graph, path, session, binding
    graph.close()


def pins(binding):
    return {"expected_generation": binding.identity, "expected_session_digest": binding.session_digest}


def test_v1_load_and_historical_advance_authenticate_both_generations(source):
    graph, path, session, binding = source
    before = path.read_bytes()
    assert load_authenticated_session(graph, path, **pins(binding)).session == session
    assert path.read_bytes() == before
    assert json.loads(before)["format_version"] == 1
    graph.epistemic.assert_claim(subject="b", predicate="memberOf", object="g2", agent="new", polarity="positive", valid=(0, 100))
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        load_authenticated_session(graph, path, **pins(binding))
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits())
    assert path.read_bytes() == before
    assert load_authenticated_session(graph, path, **pins(binding), freshness="historical").session == session
    moved = advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits(), freshness="historical")
    assert TypedFact("person", ("b",)) in moved.advance.session.facts
    moved.binding.verify(graph, load_session(path))
    assert load_authenticated_session(graph, path, **pins(moved.binding)).session == moved.advance.session
    after = path.read_bytes()
    with pytest.raises(ReasoningError, match="state"):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits(), freshness="historical")
    assert path.read_bytes() == after


@pytest.mark.parametrize("dimension", ["database_id", "commit_horizon", "valid_time_us", "provenance_token",
    "program_digest", "catalog_digest", "semantics_profile", "dictionary_id", "world", "ontology_revision",
    "source_policy_id", "mapping_id"])
def test_all_expected_generation_dimensions_fail_before_advance_or_write(source, monkeypatch, dimension):
    import gestaltdb.reasoning.adapters.authenticated_sessions as adapter
    graph, path, _, binding = source
    identity = binding.identity
    if dimension in identity.source.__dataclass_fields__:
        value = 999 if dimension in ("commit_horizon", "valid_time_us") else "other"
        wrong = replace(identity, source=replace(identity.source, **{dimension: value}))
    elif dimension in identity.context.__dataclass_fields__:
        wrong = replace(identity, context=replace(identity.context, **{dimension: "other"}))
    else:
        wrong = replace(identity, **{dimension: "other"})
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("advanced before exact generation check")
    monkeypatch.setattr(adapter, "advance_session", forbidden)
    with pytest.raises(ReasoningError, match="drifted"):
        advance_authenticated_session(graph, path, expected_generation=wrong,
            expected_session_digest=binding.session_digest, limits=ReasoningLimits())
    assert path.read_bytes() == before


def test_same_horizon_catalog_edit_and_deletion_fail_exact_state_cas(source):
    _, path, session, binding = source
    changed = replace(session, catalog=RuleCatalog([replace(e, version_id="new", ordinal=2) for e in session.catalog.entries]))
    changed_digest = save_session(changed, path, expected_session_digest=binding.session_digest)
    before = path.read_bytes()
    assert changed.commit_horizon == session.commit_horizon and changed_digest != binding.session_digest
    with pytest.raises(ReasoningError, match="state changed"):
        save_session(session, path, expected_session_digest=binding.session_digest)
    assert path.read_bytes() == before
    path.unlink()
    with pytest.raises(ReasoningError, match="state changed"):
        save_session(session, path, expected_session_digest=changed_digest)
    assert not path.exists()


def test_matching_checksum_cannot_admit_missing_independent_assertion(source):
    graph, path, session, binding = source
    retained = session.ledger.versions[:1]
    forged = replace(session, ledger=EvidenceLedger(session.ledger.scope_id, retained),
        version_logical=tuple(p for p in session.version_logical if p[0] == retained[0].version_id))
    digest = save_session(forged, path)
    before = path.read_bytes()
    assert forged.facts == session.facts
    with pytest.raises(ProvenanceMismatchError, match="evidence"):
        load_authenticated_session(graph, path, expected_generation=binding.identity, expected_session_digest=digest)
    assert path.read_bytes() == before


def test_bounds_cancellation_and_new_generation_admission_preserve_old_bytes(source):
    graph, path, _, binding = source
    before = path.read_bytes()
    with pytest.raises(ReasoningLimitError, match="max_bytes"):
        load_authenticated_session(graph, path, **pins(binding), max_bytes=1)
    with pytest.raises(ReasoningCancelledError):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits(), cancel=lambda: True)
    graph.epistemic.assert_claim(subject="b", predicate="memberOf", object="g2", agent="new", polarity="positive", valid=(0, 100))
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits(), freshness="historical",
            admission_limits=SessionAdmissionLimits(max_facts=2))
    assert path.read_bytes() == before


def test_cancel_after_temp_write_removes_only_own_temp_and_preserves_old_file(source, monkeypatch):
    import gestaltdb.reasoning.adapters.session_store as store
    _, path, session, binding = source
    before = path.read_bytes()
    flushed = []
    original = store.os.fsync
    def counted(fd):
        original(fd)
        flushed.append(True)
    monkeypatch.setattr(store.os, "fsync", counted)
    with pytest.raises(ReasoningCancelledError):
        save_session(session, path, expected_session_digest=binding.session_digest, cancel=lambda: bool(flushed))
    assert path.read_bytes() == before and not list(path.parent.glob(".session.json.tmp.*"))


def test_reentrant_source_move_before_replace_does_not_persist_stale_new_generation(source, monkeypatch):
    import gestaltdb.reasoning.adapters.session_store as store
    graph, path, _, binding = source
    before = path.read_bytes()
    original = store._encoded_bytes
    def moved(session):
        graph.epistemic.assert_claim(subject="new", predicate="memberOf", object="g2", agent="new", polarity="positive", valid=(0, 100))
        return original(session)
    monkeypatch.setattr(store, "_encoded_bytes", moved)
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits())
    assert path.read_bytes() == before


def test_concurrent_cooperating_file_writer_waits_then_fails_old_state_pin(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import gestaltdb.reasoning.adapters.authenticated_sessions as adapter
    graph, path, session, binding = source
    graph.epistemic.assert_claim(subject="b", predicate="memberOf", object="g2", agent="new", polarity="positive", valid=(0, 100))
    entered, release, attempted, finished = Event(), Event(), Event(), Event()
    original = adapter.advance_session
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    def writer():
        attempted.set()
        try:
            save_session(session, path, expected_session_digest=binding.session_digest)
        finally:
            finished.set()
    monkeypatch.setattr(adapter, "advance_session", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        advanced = pool.submit(advance_authenticated_session, graph, path, **pins(binding),
            limits=ReasoningLimits(), freshness="historical")
        try:
            assert entered.wait(5)
            writing = pool.submit(writer)
            assert attempted.wait(5) and not finished.wait(0.05)
        finally:
            release.set()
        result = advanced.result(timeout=5)
        with pytest.raises(ReasoningError, match="state changed"):
            writing.result(timeout=5)
    assert _digest(session_to_dict(load_session(path))) == result.binding.session_digest


def test_cross_process_exact_state_cas_has_one_winner(source):
    _, path, _, binding = source
    code = '''
from dataclasses import replace
import sys
from gestaltdb.reasoning.adapters.session_store import load_session, save_session
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.model import ReasoningError
s = load_session(sys.argv[1])
s = replace(s, catalog=RuleCatalog([replace(e, version_id=sys.argv[3]) for e in s.catalog.entries]))
try:
    save_session(s, sys.argv[1], expected_session_digest=sys.argv[2])
except ReasoningError:
    print('rejected')
else:
    print('saved')
'''
    workers = [subprocess.Popen([sys.executable, "-c", code, str(path), binding.session_digest, tag],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for tag in ("one", "two")]
    results = []
    for worker in workers:
        out, err = worker.communicate(timeout=20)
        assert worker.returncode == 0, err
        results.append(out.strip())
    assert sorted(results) == ["rejected", "saved"]
    load_session(path)


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_authenticated_resume_after_process_restart(tmp_path, backend, module):
    pytest.importorskip(module)
    db, path = tmp_path / "db", tmp_path / "session.json"
    graph = GraphDB.create(db, backend=backend, serializer="json")
    try:
        graph.epistemic.assert_claim(subject="a", predicate="memberOf", object="g1", agent="one", polarity="positive", valid=(0, 100))
        session = start_session(graph, PROGRAM, catalog_for_typed_program(PROGRAM),
            scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
        binding = bind_session_generation(graph, session)
        save_session(session, path)
        graph.epistemic.assert_claim(subject="b", predicate="memberOf", object="g2", agent="new", polarity="positive", valid=(0, 100))
    finally:
        graph.close()
    code = '''
import json,sys
from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.generation import GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.model import ReasoningLimits
from gestaltdb.reasoning.adapters.authenticated_sessions import advance_authenticated_session
raw=json.loads(sys.argv[3]); raw['source']=TemporalSourceIdentity(**raw['source']); raw['context']=InputContext(**raw['context'])
graph=GraphDB.open(sys.argv[1])
try:
    result=advance_authenticated_session(graph, sys.argv[2], expected_generation=GenerationIdentity(**raw),
        expected_session_digest=sys.argv[4], freshness='historical', limits=ReasoningLimits())
    print(result.binding.session_digest)
finally:
    graph.close()
'''
    done = subprocess.run([sys.executable, "-c", code, str(db), str(path), json.dumps(asdict(binding.identity)), binding.session_digest],
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    resumed = load_session(path)
    assert TypedFact("person", ("b",)) in resumed.facts
    assert _digest(session_to_dict(resumed)) == done.stdout.strip()


def test_reentrant_same_horizon_edit_is_not_overwritten(source, monkeypatch):
    import gestaltdb.reasoning.adapters.authenticated_sessions as adapter
    graph, path, session, binding = source
    changed = replace(session, catalog=RuleCatalog([replace(e, version_id="changed") for e in session.catalog.entries]))
    original = adapter.advance_session
    competing = []
    def edit(*args, **kwargs):
        save_session(changed, path)
        competing.append(path.read_bytes())
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, "advance_session", edit)
    with pytest.raises(ReasoningError, match="state changed"):
        advance_authenticated_session(graph, path, **pins(binding), limits=ReasoningLimits())
    assert path.read_bytes() == competing[0]


def test_replace_failure_preserves_v1_file_and_cleans_own_temp(source, monkeypatch):
    import gestaltdb.reasoning.adapters.session_store as store
    _, path, session, binding = source
    before = path.read_bytes()
    def interrupted(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(store.os, "replace", interrupted)
    with pytest.raises(OSError, match="replace failed"):
        save_session(session, path, expected_session_digest=binding.session_digest)
    assert path.read_bytes() == before and not list(path.parent.glob(".session.json.tmp.*"))


def test_authenticated_session_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-authenticated-sessions.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})
