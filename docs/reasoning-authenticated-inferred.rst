Authenticated inferred sampler artifacts
============================================================

``gestaltdb.reasoning.adapters.authenticated_inferred_sampler`` aligns the
existing inferred sampler consumer with :doc:`reasoning-generation-identity`.
It does not add sampling algorithms, execute reasoning automatically, publish
claims, or change the default executor.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.model import ReasoningLimits, Variable
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
   from gestaltdb.reasoning.adapters.session_generation import bind_session_generation, catalog_for_typed_program
   from gestaltdb.reasoning.adapters.authenticated_inferred_sampler import (
       InferredSnapshotSelection, build_authenticated_inferred_snapshot,
       load_authenticated_inferred_snapshot,
   )
   from gestaltdb.sampling.engine import SamplerEngine

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
           pins = dict(expected_generation=binding.identity, expected_session_digest=binding.session_digest)
           selection = InferredSnapshotSelection("person", ("memberOf",))
           path = Path(root) / "inferred"
           build_authenticated_inferred_snapshot(graph, session, path, **pins, selection=selection)
           snapshot = load_authenticated_inferred_snapshot(graph, session, path, **pins, selection=selection)
           assert snapshot.num_edges == 1
           engine = SamplerEngine(snapshot, seed=7)
           batch = engine.sample_neighbors([snapshot.node_int("a")], fanout=10, direction="out")
           assert batch.neighbor_nodes.size == 1
       finally:
           graph.close()

Exact admission and explicit selection
------------------------------------------

Both APIs require a caller-owned exact ``GenerationIdentity`` and
``expected_session_digest``. They independently authenticate the session's
source through ``bind_session_generation`` and compare both pins before building
or admitting an artifact. An altered session with equal logical facts but a
missing independent assertion version fails source admission. Source admission
does **not** independently prove correctness of arbitrary supplied closure or
supports. The caller must trust the session computation and its own saved pins.

``InferredSnapshotSelection`` separately names one unary node predicate and a
nonempty explicit collection of binary edge predicates. Edge predicates are
deduplicated and sorted; their input ordering is irrelevant. This projection
selection is not the source ``ClaimSelection`` or ``ClaimMapping``, and never
changes their identities. Node membership comes from string-valued unary facts;
selected binary string-valued facts are included only when their source is a
selected node. Target endpoints are included, even without the unary fact.
Every selected fact yields one edge with unit weight, irrespective of the number
of assertion/support versions. This preserves the existing consumer's semantics;
it is not an induced subgraph, multi-hop traversal, or an all-support graph.

Default ``freshness="latest"`` rejects any newer source commit, even an unrelated
one. Explicit ``freshness="historical"`` authenticates the exact pinned prefix,
allowing old sessions/artifacts without pretending they are latest. A newer
session uses new caller pins and a new artifact path; old artifacts are never
updated. Temporal point, current graph and finite range representations remain
distinct and cannot be relabelled as this typed-session consumer.

Complete metadata and independent hydration
-------------------------------------------------

Sampler format-v2 metadata contains ``authenticated_reasoning_generation`` with
consumer contract version ``1``: the full shared generation identity, full
authenticated read provenance (including backend/decoder identity), world,
valid point, source selection and mapping, exact session-state digest, canonical
artifact selection, projection representation, and selected node/edge counts.
The snapshot ID is deterministically bound to that contract. Existing shared
generation digests and legacy snapshot/session formats are unchanged.

Hydration re-admits the source and session, reconstructs the complete expected
v2 artifact in temporary storage, then compares the exact file set and every
byte, including metadata, completion manifest, topology, lookup, CSR and policy
arrays. It returns the byte-equivalent, eagerly loaded reconstruction, with its
path set to the admitted artifact, avoiding a second read of caller arrays after
comparison. Rewriting stored
checksums after altering arrays does not bypass this independent comparison.
This is intentionally expensive re-admission, not a fast cache hit. It requires
the exact supported serialization representation: incompatible future NumPy or
snapshot encoding changes fail closed and require explicit rebuilding, not
silent migration. There is no authenticated memmap shortcut.

The legacy ``inferred_sampler.build_inferred_snapshot`` remains available with
its original partial mapping checks and ``reasoning_generation`` metadata.
Those checks are **not** shared generation/state admission or independent source
authentication. Such artifacts are not silently upgraded by authenticated load;
build a new artifact explicitly. Likewise raw ``SamplerSnapshot.load`` and
``SamplerEngine.load`` provide format/integrity checks, not this source/session
admission. To sample after admission, pass the returned snapshot directly to
``SamplerEngine``, as above.

Bounds, cancellation and immutable files
-------------------------------------------------

``SessionAdmissionLimits`` bounds source candidates and session state.
``InferredSnapshotLimits`` bounds projected nodes (including endpoints), selected
edges and total serialized artifact bytes. File sets are also capped at 128
regular nonsymlink files. Byte comparison checks cancellation per 64 KiB chunk;
projection checks between facts. Native NumPy operations, sorting, backend
authentication, lock waits and snapshot I/O are not interrupted mid-call.
Serialized-byte checks run after constructing temporary expected arrays; these
are not hard RSS, temporary-disk or native-operation work/deadline bounds.

Build holds the temporal writer guard and a cooperating artifact-path sidecar
lock while admitting/staging and rechecking source freshness/store/decoder before
rename. A populated or empty existing target is rejected, not overwritten.
Cancellation/failure before rename removes only that invocation's temporary
directory. A failure after rename may already have installed the complete
artifact; callers reload with trusted pins rather than assume rollback. This
does not add a power-loss durability protocol to sampler snapshots.

Load never writes or repairs supplied artifacts and returns eager arrays.
Snapshot directories must remain immutable under cooperating APIs during
verification/load; hostile direct filesystem mutation, hardlink/path alias
replacement and network-filesystem locking anomalies are outside this contract.
The database guard coordinates normal temporal writers, not raw storage edits.
Files and graph state may change after return; the admitted in-memory snapshot
has no live database handle and no continuing latest-source guarantee.
