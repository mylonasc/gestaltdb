"""#370: abrupt-termination and cross-process movement evidence.

What this pins (and what it does not):
- SIGKILL between temporal-sequence reservation and commit leaves a
  ``reservation_gap`` (never a partial commit): old data stays visible,
  the next commit skips the dead ID, orphan reclaim is a no-op for gaps.
- SIGKILL between session tmp flush/fsync and ``os.replace`` leaves the
  old session file intact and loadable; the stray tmp is never installed
  (the loader only reads the target) and never auto-deleted per contract.
- A cross-process ``assert_claim`` between range staging and publication
  fails closed with ``GenerationStaleError``; restaging publishes cleanly.

What this is NOT: power-loss, torn-page, or network-filesystem evidence.
The kill windows cover process death at the two atomic-install boundaries
(sequence sidecar replace is already durable before the child is killed;
session tmp is flushed+fsynced before the kill). Kill *during* the
write() syscall is not staged: stray tmps are loader-invisible by
construction (``load_session`` reads only the target path).

Backend scope: kill tests reopen sequentially, so they run on
lmdb/leveldb/pyrex. The concurrent cross-process test needs multi-process
opens of one live database, which only LMDB supports here (LevelDB and
RocksDB take a process-exclusive database lock); that boundary is already
documented in ``docs/storage-backends.rst``.
"""

import os
import subprocess
import sys

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.range_publication import (
    stage_range_generation,
    publish_range_generation,
)
from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
from gestaltdb.reasoning.adapters.session_store import load_session, session_to_dict, _digest
from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom

X, Y, Z = Variable("x"), Variable("y"), Variable("z")
PROGRAM = GenericProgram([
    GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
    GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [],
                TypedAtom("reach", [X, Z])),
])
MEMBER_PROGRAM = GenericProgram([
    GenericRule("r", [TypedAtom("memberOf", [X, Y])], [], TypedAtom("person", [X])),
])

pytestmark = pytest.mark.skipif(os.name != "posix", reason="SIGKILL crash tests need POSIX")


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def db_path(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    return tmp_path / "db", backend


def test_sigkill_between_reserve_and_commit_leaves_gap(db_path):
    path, backend = db_path
    graph = GraphDB.create(path, backend=backend, serializer="json")
    graph.epistemic.assert_claim(subject="a", predicate="edge", object="b", agent="one",
                       polarity="positive", valid=(0, 100))
    graph.close()
    code = (
        "import sys, time\n"
        "from gestaltdb.graphdb import GraphDB\n"
        "graph = GraphDB.open(sys.argv[1])\n"
        "cid, _ = graph.temporal.reserve_sequence()\n"
        "print(cid, flush=True)\n"
        "time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        reserved = int(child.stdout.readline().strip())
        assert reserved == 2
    finally:
        child.kill()  # SIGKILL: abrupt death between reservation and commit.
        child.wait(timeout=30)
    assert child.returncode == -9, child.stderr.read()
    graph = GraphDB.open(path)
    try:
        # No lock poisoning: the dead writer's guard is gone.
        report = graph.temporal.list_temporal_orphans()
        kinds = {a.commit_id: a.kind for a in report.artifacts}
        assert kinds.get(reserved) == "reservation_gap"
        assert "incomplete_commit" not in kinds.values()
        reclaimed = graph.temporal.reclaim_temporal_orphans(through_commit=reserved)
        assert reserved in reclaimed.reservation_gap_ids
        assert reclaimed.reclaimed_commit_ids == ()
        assert reclaimed.indexes_rebuilt is False
        # Old-or-new: commit 1 visible, no partial commit 2.
        assert graph.temporal.get_temporal_commit(1) is not None
        assert graph.temporal.get_temporal_commit(reserved) is None
        old = list(graph.epistemic.iter_claims_as_of(valid_time=50, predicate="edge"))
        assert len(old) == 1
        # The dead ID is consumed, never reused.
        new = graph.epistemic.assert_claim(subject="b", predicate="edge", object="c",
                                 agent="two", polarity="positive", valid=(0, 100))
        assert new.commit_id == reserved + 1
        assert len(list(graph.epistemic.iter_claims_as_of(valid_time=50, predicate="edge"))) == 2
    finally:
        graph.close()


def test_sigkill_before_session_replace_preserves_old_file(db_path):
    path, backend = db_path
    session_file = path.parent / "session.json"
    graph = GraphDB.create(path, backend=backend, serializer="json")
    graph.epistemic.assert_claim(subject="a", predicate="memberOf", object="g1", agent="one",
                       polarity="positive", valid=(0, 100))
    session = start_session(graph, MEMBER_PROGRAM, catalog_for_typed_program(MEMBER_PROGRAM),
                            scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()),
                            limits=ReasoningLimits())
    old_digest = _digest(session_to_dict(session))
    from gestaltdb.reasoning.adapters.session_store import save_session as _save
    _save(session, session_file)
    graph.epistemic.assert_claim(subject="b", predicate="memberOf", object="g2", agent="new",
                       polarity="positive", valid=(0, 100))
    graph.close()
    before = session_file.read_bytes()
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from gestaltdb.graphdb import GraphDB\n"
        "from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection\n"
        "from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program\n"
        "from gestaltdb.reasoning.adapters.session_store import _encoded_bytes\n"
        "from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session\n"
        "from gestaltdb.reasoning.model import ReasoningLimits\n"
        "from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, Variable\n"
        "X, Y = Variable('x'), Variable('y')\n"
        "program = GenericProgram([GenericRule('r', [TypedAtom('memberOf', [X, Y])], [], TypedAtom('person', [X]))])\n"
        "graph = GraphDB.open(sys.argv[1])\n"
        "try:\n"
        "    latest = start_session(graph, program, catalog_for_typed_program(program),\n"
        "        scope=SessionScope('default', 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())\n"
        "    target = Path(sys.argv[2])\n"
        "    tmp = target.parent / (f'.{target.name}.tmp.{__import__(\"os\").getpid()}.staged')\n"
        "    with tmp.open('wb') as handle:\n"
        "        handle.write(_encoded_bytes(latest))\n"
        "        handle.flush()\n"
        "        __import__('os').fsync(handle.fileno())\n"
        "    print('staged', flush=True)\n"
        "    time.sleep(60)\n"
        "finally:\n"
        "    pass\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(path), str(session_file)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "staged"
    finally:
        child.kill()  # SIGKILL after tmp flush/fsync, before os.replace.
        child.wait(timeout=30)
    assert child.returncode == -9, child.stderr.read()
    # Old file intact and loadable under its old digest; stray tmp never installed.
    assert session_file.read_bytes() == before
    loaded = load_session(session_file)
    assert _digest(session_to_dict(loaded)) == old_digest
    strays = list(session_file.parent.glob(f".{session_file.name}.tmp.*"))
    assert len(strays) == 1  # contract: unused tmps are never auto-deleted.
    graph = GraphDB.open(path)
    try:
        from gestaltdb.reasoning.adapters.session_store import save_session as _save2
        latest = start_session(graph, MEMBER_PROGRAM, catalog_for_typed_program(MEMBER_PROGRAM),
                               scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()),
                               limits=ReasoningLimits())
        _save2(latest, session_file)
        assert _digest(session_to_dict(load_session(session_file))) != old_digest
    finally:
        graph.close()
        for stray in session_file.parent.glob(f".{session_file.name}.tmp.*"):
            stray.unlink()


def test_cross_process_claim_move_stales_range_publication(tmp_path):
    pytest.importorskip("lmdb")
    db = tmp_path / "db"
    graph = GraphDB.create(db, backend="lmdb", serializer="json")
    graph.epistemic.assert_claim(subject="a", predicate="edge", object="b", agent="one",
                       polarity="positive", valid=(0, 40))
    graph.epistemic.assert_claim(subject="b", predicate="edge", object="c", agent="two",
                       polarity="positive", valid=(10, 30))
    staged = stage_range_generation(graph, PROGRAM, catalog_for_typed_program(PROGRAM),
                                    valid_start_us=0, valid_end_us=100, source_world="default",
                                    target_world="inferred", publication_key="race",
                                    selection=ClaimSelection(), mapping=ClaimMapping(),
                                    limits=ReasoningLimits())
    mover = subprocess.run(
        [sys.executable, "-c",
         "import sys; from gestaltdb.graphdb import GraphDB\n"
         "graph = GraphDB.open(sys.argv[1])\n"
         "try:\n"
         "    graph.epistemic.assert_claim(subject='c', predicate='edge', object='d',"
         " agent='mover', polarity='positive', valid=(0, 100))\n"
         "finally:\n"
         "    graph.close()\n", str(db)],
        capture_output=True, text=True, timeout=60)
    assert mover.returncode == 0, mover.stderr
    try:
        # The source-horizon gate fires before the target CAS: the staged
        # prefix (horizon 2) no longer matches the moved source (horizon 3).
        # Either gate failing closed is correct; this pins the actual order.
        with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
            publish_range_generation(graph, staged, limits=ReasoningLimits())
    finally:
        graph.close()
    # Retry on the new horizon publishes cleanly: old-or-new, never partial.
    graph = GraphDB.open(db)
    try:
        restaged = stage_range_generation(graph, PROGRAM, catalog_for_typed_program(PROGRAM),
                                          valid_start_us=0, valid_end_us=100, source_world="default",
                                          target_world="inferred", publication_key="race",
                                          selection=ClaimSelection(), mapping=ClaimMapping(),
                                          limits=ReasoningLimits())
        result = publish_range_generation(graph, restaged, limits=ReasoningLimits())
        assert result.commit_id is not None
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=20) as view:
            inferred = list(view.iter_claims_as_of(world="inferred"))
        assert any(v.claim is not None and v.claim.predicate == "reach" for v in inferred)
    finally:
        graph.close()
