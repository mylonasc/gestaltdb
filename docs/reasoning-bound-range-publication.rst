Authenticated interval-batch publication
============================================================

``gestaltdb.reasoning.adapters.range_publication`` connects authenticated
:doc:`reasoning-range-admission` computation to guarded publication. It is
separate from raw caller-derived ``stage_range``: staging here owns extraction
and evaluation, and publishing re-admits exact source evidence and closure.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.model import ReasoningLimits, Variable
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
   from gestaltdb.reasoning.adapters.range_publication import stage_range_generation, publish_range_generation

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([GenericRule("r", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y]))])
   catalog = catalog_for_typed_program(program)
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path, backend="lmdb", serializer="json")
       try:
           graph.epistemic.assert_claim(subject="a", predicate="edge", object="b", agent="example",
                              polarity="positive", valid=(10, 20))
           staged = stage_range_generation(graph, program, catalog,
               valid_start_us=0, valid_end_us=30, source_world="default", target_world="inferred",
               publication_key="demo", selection=ClaimSelection(), mapping=ClaimMapping(), limits=ReasoningLimits())
           published = publish_range_generation(graph, staged, limits=ReasoningLimits(),
               expected_generation=staged.result.binding.identity)
           assert published.asserted == 2  # base edge plus derived reach
           assert len(graph.temporal.get_temporal_commit(published.commit_id).versions) == 2
           with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=20) as view:
               assert tuple(view.iter_claims_as_of(world="inferred")) == ()
       finally:
           graph.close()

Admission, freshness and atomicity
------------------------------------------

``stage_range_generation`` evaluates the actual typed program/catalog against
one authenticated finite range and captures the target state under that same
prefix. It accepts no precomputed triples/result parameter. Only binary entity
outputs are publishable: unary/n-ary/literal results require an explicit
conversion and fail before writes, rather than being silently discarded.
Source and target worlds must differ and source selection must exclude derived
inputs. All mapped base and derived facts are included.

``publish_range_generation`` holds the shared temporal writer guard through:

* Explicit orphan audit; incomplete publication needs caller-managed recovery.
* Source authentication and, by default, rejection of any horizon movement.
* Complete source/result re-admission, including fresh closure/support comparison.
* Recalculation of interval output and content identity, rejecting altered stages.
* Exact target-version/history comparison and complete bounded write preparation.
* Final source/horizon checks and **one logical temporal commit** for every
  assertion, correction and retraction in the interval batch.

Optional ``expected_generation`` pins every shared source/program/catalog/policy/
range/profile/representation dimension before entering the writer guard. It
does not bypass any checks. Explicit ``freshness="historical"`` permits the
original source prefix, never a changed target: target CAS remains mandatory.
The target fingerprint includes tombstones/history as well as active winners,
rejecting empty-target ABA movement. A second source/target movement during
preparation fails even in historical mode. Retrying a moved stage requires
explicit restaging, not silent rebasing.

Normal temporal readers observe old-or-new commit visibility, not a successful
partial interval batch. Backend failures can leave unmarked artifacts on
nontransactional stores; reads fail closed and retries refuse automatic repair.
Use the existing orphan audit/reclamation/rebuild workflow with explicit caller
consent. A failure after visibility may already have committed the whole batch;
restaging detects unchanged content and avoids a duplicate commit.

Namespaces, intervals and provenance
------------------------------------

The reserved batch namespace binds publication key, source/target worlds and
requested validity range. Each distinct fact-validity interval has a distinct
source namespace, so disjoint intervals never compete as versions of one
logical claim. Full target-history discovery includes obsolete intervals that
disappeared from the new closure; these are retracted in the same commit.
Reserved namespace rows with incompatible metadata or partial externally
changed validity fail closed. Unrelated namespaces are not reconciled.

Fact validity uses its exact normalized interval union: gaps are never filled
or widened to a min/max envelope. Claim provenance and commit metadata record
the shared reasoning generation, source horizon/token/range, actual program and
catalog digests, context, selection/mapping, profile, representation and freshness.
These records identify the authenticated computation; they are not independent
verification of the engine implementation.

Content identity binds exact input version/fact/interval evidence, program,
catalog, policies and output, but not the database horizon alone. Therefore a
previous publication advancing its own database horizon does not churn equal
output. Unchanged claims retain their truthful original generation provenance;
new/corrected claims and new commit metadata identify the current computation.
Changing the requested range or key selects another reserved namespace; it does
not implicitly retire a caller's other publication ranges.

Bounds and costs
----------------

``RangePublicationLimits`` bounds all scanned target-history claim versions,
retained managed logical interval IDs (including retired IDs), output rows and
prepared writes. ``RangeSourceLimits`` and ``ReasoningLimits`` retain their
range extraction/evaluation/re-admission semantics. Cancellation is cooperative
before commit; it cannot interrupt hashing/backend operations or a committing
kernel. Validation/limit/cancellation failures before commit leave the temporal
history unchanged. Bounds are not byte/RSS/deadline limits.

Publishing deliberately repeats source extraction and complete range inference
under the writer guard. Target discovery also scans history, not just active
claims at the requested start. This correctness-first integration has no
throughput/latency or short-lock-duration claim. Existing point publication and
raw range publication remain unchanged; no automatic executor/native adoption,
Cypher rewrite, universal cache admission or sampler expansion is implied.
