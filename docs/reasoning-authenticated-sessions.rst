Authenticated durable-session resume
============================================================

``gestaltdb.reasoning.adapters.authenticated_sessions`` connects existing v1
session files to :doc:`reasoning-generation-identity` admission. Callers provide
both an exact shared ``GenerationIdentity`` and ``expected_session_digest``
from trusted application state. The file's own checksum is not that authority.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.model import ReasoningLimits, Variable
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
   from gestaltdb.reasoning.adapters.session_generation import bind_session_generation, catalog_for_typed_program
   from gestaltdb.reasoning.adapters.session_store import save_session
   from gestaltdb.reasoning.adapters.authenticated_sessions import load_authenticated_session, advance_authenticated_session

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([GenericRule("r", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x]))])
   with TemporaryDirectory() as root:
       graph = GraphDB.create(Path(root) / "db", backend="lmdb", serializer="json")
       try:
           graph.assert_claim(subject="a", predicate="memberOf", object="g", agent="example",
                              polarity="positive", valid=(0, 100))
           session = start_session(graph, program, catalog_for_typed_program(program),
               scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
           binding = bind_session_generation(graph, session)
           path = Path(root) / "session.json"
           save_session(session, path)
           loaded = load_authenticated_session(graph, path, expected_generation=binding.identity,
               expected_session_digest=binding.session_digest)
           assert loaded.session == session
           graph.assert_claim(subject="b", predicate="memberOf", object="g", agent="example",
                              polarity="positive", valid=(0, 100))
           moved = advance_authenticated_session(graph, path, expected_generation=binding.identity,
               expected_session_digest=binding.session_digest, freshness="historical", limits=ReasoningLimits())
           assert TypedFact("person", ("b",)) in moved.advance.session.facts
           moved.binding.verify(graph, moved.advance.session)
       finally:
           graph.close()

Admission and freshness
------------------------------------------

``load_authenticated_session`` bounds file reads, validates the unchanged v1
format and internal consistency, compares the exact caller state pin, then
independently authenticates all active assertion versions through
``bind_session_generation``. Database/horizon/program/catalog/world/valid-point/
selection/mapping/profile/representation drift fails compatibility before an
advance or write. Equal logical facts with a missing independent assertion
version do not pass admission. Read-only loading creates no sidecar lock and
does not promise that source/file state stays latest after return.

The default ``freshness="latest"`` rejects a source that advanced beyond the
saved session. Explicit ``freshness="historical"`` admits the exact old prefix.
For advancement, historical mode authenticates the **old input** only: the
computed new session is always independently admitted against latest source
before persistence. No stale new output is relabelled as current.

``advance_authenticated_session`` holds the database temporal writer guard,
then a cooperating session-file lock, across load/admission, existing incremental
advance or explicit rebuild, new-generation admission and exact-state file CAS.
Normal temporal writers cannot change the source through that boundary. Source
store/decoder rebinding and reentrant source movement before replacement fail
closed. It writes no database claims and grants no publication permit.

These are source and state guarantees, not independent proof that arbitrary
caller-supplied session closure/supports were computed correctly. Existing
typed-point admission/profile restrictions remain: mutable graph/range/session
representations are not interchangeable. Pins must come from the caller's
trusted binding, not be adopted blindly from an untrusted artifact.

Persistence compatibility and races
------------------------------------------

Existing v1 JSON files and their canonical session digests are unchanged.
``save_session`` now serializes cooperating threads/processes through a hidden
sidecar lock and accepts additive ``expected_session_digest``. Unlike a horizon
alone, this pin rejects same-horizon edits and deletion. The legacy
``advance_persisted_session`` also uses an exact loaded-state comparison when
saving, but does not silently acquire independent source-admission guarantees.

Writes use a unique temporary file, flush/fsync, final state comparison and
atomic replacement. The parent directory is fsynced where supported. Failures
or cancellation before replacement preserve the existing file bytes and clean
up only that invocation's temporary file. A process crash can leave an unused
temporary file; it is not loaded as committed state and is not automatically
deleted. Failures after replacement can already have installed the new complete
file; callers reload against trusted pins rather than assuming rollback.

Lock guarantees require cooperating APIs and a stable canonical path. Direct
filesystem writers, hardlink aliases, path/symlink replacement by outsiders and
network-filesystem lock/durability anomalies are not covered. Sidecar lock files
are coordination metadata, not source authentication. Locks can block;
cancellation does not interrupt a lock wait or native/backend/fsync operation.

``max_bytes`` bounds bytes read before JSON decoding and final encoded file
size, not all serialization/parser memory. ``SessionAdmissionLimits`` bounds
versions/facts/supports/prefix length and admission candidates; existing
``ReasoningLimits`` and ``max_advance_commits`` govern advancement. Authentication
and state encoding remain explicit potentially expensive work, not a fast cache
lookup or a hard RSS/deadline guarantee. Source-independent specialized-program
caches remain keyed by program/goal and do not need source-horizon invalidation.
