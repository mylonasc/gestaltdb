Authenticated native historical claim locators
===============================================

``reasoning.adapters.native_locator_claims.PinnedNativeLocatorClaimSource`` is
an explicit alternative to the :doc:`reasoning-native-claims` full-record source.
It uses native temporal index-prefix scans and point-materialized versions,
including complete correction/retraction histories, under the same point-view
policy, evidence and logical context contract. Ordinary extraction and the
full-record native source remain unchanged; there is no automatic selection.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
   from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
   from gestaltdb.reasoning.adapters.native_locator_claims import (
       NativeLocatorLimits, PinnedNativeLocatorClaimSource,
   )
   from gestaltdb.reasoning.extraction import ExtractionRequest

   with TemporaryDirectory() as root:
       graph = GraphDB.create(Path(root) / "db", backend="pyrex", serializer="json")
       try:
           first = graph.epistemic.assert_claim(subject="alice", predicate="worksFor", object="acme",
               agent="registry", polarity="positive", valid=(0, 100))
           corrected = graph.epistemic.correct_claim(first.logical_id, confidence=0.9,
               supersedes_version_id=first.version_id)
           with graph.temporal.open_read_view(
               graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50
           ) as view:
               options = dict(request=ExtractionRequest("default", predicates=["worksFor"], subject="alice"),
                   ontology_revision="example-revision", selection=ClaimSelection(), mapping=ClaimMapping())
               with PinnedNativeLocatorClaimSource(view, **options,
                       native_limits=NativeLocatorLimits(max_authenticated_records=1000)) as native:
                   rows = tuple(e for batch in native.iter_batches() for e in batch.evidence)
                   assert len(rows) == 1 and rows[0].reference_id == corrected.version_id
                   assert native.native_profile.authenticated_records == 2
                   assert native.native_profile.candidate_claims == 1
               with PinnedBatchedClaimSource(view, **options) as ordinary:
                   assert rows == tuple(e for batch in ordinary.iter_batches() for e in batch.evidence)
       finally:
           graph.close()

Admission and historical completeness
---------------------------------------

The source requires plain nontransactional PyRex with native Arrow scans
(``pyrex-rocksdb>=0.5.0`` and the Arrow extra), an initialized managed database,
current temporal indexes, and a caller-owned authenticated ``GraphReadView``.
Unsupported storage, stale indexes and transactional native claim admission do
not silently fall back, rebuild or broaden the selected source. PyRex 0.6.0 raw
transactional scan capability is a separate :doc:`reasoning-extraction` contract.

Every pass holds the existing temporal writer guard, verifies the source,
authenticates the complete pinned commit catalog, and establishes the expected
index key/locator memberships from canonical version values. This full catalog
work is deliberate: checksums and raw snapshot tokens do not prove that a
secondary index omitted no claims. An incomplete descriptor cannot be crossed
to expose later commits. Descriptor-free gaps have no visible catalog members;
this adapter performs no orphan recovery or index repair.

All native scans in a pass borrow **one default-family snapshot**. The world
prefix is intersected with the union of requested predicate prefixes and, when
supplied, the subject prefix. Each scan pushes an inclusive valid-start bound
to storage; records starting after the point are not needed. Native keys must
be strictly ordered, in the exact prefix/range, and match authenticated expected
key/locator membership. Missing, substituted, duplicate or malformed entries
fail before evidence is returned. Later-commit entries beyond an old pinned
horizon do not become evidence; they still count toward raw index work.

Candidate logical IDs are resolved against their **complete point-eligible
logical-history prefix**, not only matching assertions. Retractions and
corrections are included even when absent from the original claim-dimension
index. Every admitted history locator is point-materialized and compared with
its exact authenticated claim version. Winners are selected by commit/ordinal
within half-open validity intervals and must agree with the full catalog.
Removing a retraction locator cannot resurrect an earlier assertion.

The adapter also retains the existing unfiltered grouped canonical point
iterator and successful-exhaustion conformance check against the full pinned
history. It does not replace canonical point semantics with a benchmark-only
assertion decoder. Before/after source verification and inherited batch
verification remain in place. The shared batched policy then intersects
agent/source scopes, selects positive entity claims in the requested world,
handles derived-claim opt-in and maps predicates with exact version-ID evidence.
Distinct independent supports remain distinct evidence, not all-support proofs.

Physical pushdown versus authentication costs
----------------------------------------------

World/predicate/subject **index-prefix transfer** is native storage pushdown;
predicate alternatives are a union and simultaneous bindings intersect logical
IDs. This is not arbitrary predicate-expression pushdown. Object bindings,
agent/source policy and derived-claim policy remain post-selection filters.
``profile.pushdown`` describes this split rather than labelling every filter
physical. Logical ``InputContext`` identity stays identical to ordinary
extraction for the same effective request, mapping, policy, ontology and view.

Full catalog authentication still reads and decodes all pinned records,
including unrelated worlds, literals, negatives and other record kinds. The
unfiltered canonical conformance read is retained too. Native index transfer
can be selective without making total extraction selective or avoiding Python
decoding. One logical-history native scan per candidate has overhead of its
own. This implementation makes **no speedup, RSS, scale or adoption claim**;
the existing assertion-only ``native-locator`` benchmark does not measure it,
and earlier full-record native results cannot be transferred to this source.
Any measurement must use this actual adapter and charge authentication,
snapshot/locator transfer, history resolution, materialization and policy work.

``NativeLocatorLimits`` inherits positive physical bounds from
``NativeClaimLimits`` and adds ``max_authenticated_records``. The latter caps
the complete catalog, not only claims matching the pushed prefixes.
``max_logical_claims`` bounds distinct point-eligible logical claim histories
across the authenticated catalog. ``max_raw_rows`` is cumulative across all
dimension and logical-history native scans, including repeated locator visits;
an individual scan also has the native row limit. Per-batch row/byte caps remain
independent. Commit authentication and backend calls are not interrupted
mid-operation: a commit is validated before its length is charged, so none of
these limits is a hard RSS, backend-read or elapsed-time guarantee.
Existing ``ClaimSourceLimits`` and request output bounds still apply separately.

``native_profile`` is a ``NativeLocatorProfile``, intentionally different from
the full-record ``NativeClaimProfile``. It reports all authenticated records,
cumulative raw index rows, candidate logical IDs, point-materialized history
records, resolved non-retracted candidates and native scan count. Resolved
candidates precede the shared policy filters; common ``profile.rows`` counts
actual output evidence. These counters are neither mutually interchangeable
nor an instrumented total of source/engine work. Bytes copied and RSS are not
inferred from them.

Actual-adapter measurement harness
----------------------------------

``benchmarks.reasoning_locator_pipeline`` measures this production adapter as
``native-authenticated-locator``, separately from ``native-full-record`` and
``ordinary``. It never invokes the assertion-only benchmark decoder. For example:

.. code-block:: sh

   uv run --extra rocksdb --extra arrow python -m benchmarks.reasoning_locator_pipeline --rows 1000 --times 10 30 50 70 90 --repeats 1 --output /tmp/opencode/locator-smoke.json
   uv run --extra rocksdb --extra arrow python -m benchmarks.reasoning_locator_pipeline --rows 100000 --times 30 50 --repeats 3 --timeout 600 --output /tmp/opencode/locator-100k.json

The fixture has variable bounded entity IDs and compressible provenance padding,
one correction per five assertions over ``[20, 80)``, and one retraction per seven
over ``[40, 60)``. Named ``all``, ``predicate`` (one quarter of assertions) and
``subject`` (one logical history, possibly retracted) selections run at explicit
valid-time points. Exact fact/version-ID evidence is checked against an independent
winner oracle assembled from write receipts, not from the extraction being timed.
No inference, support discovery or publication cost is measured.

Fixture loading is a separate process. Extraction workers include complete view
and source authentication, native transfer, history point materialization, canonical
conformance, source closure and final verification in the timed pipeline. Reports
retain distinct native profiles, source revision/dirty state, harness hashes, runtime
versions, repeated timing summaries and whole-worker peak RSS. Warmup uses the same
method; neither fresh workers nor reopened handles imply OS-cold caches. Failed or
timed-out workers retain stage diagnostics in incomplete reports without quantiles
or an adoption decision. These commands are a measurement protocol, not evidence
that a successful 100K locator comparison or speedup has already been obtained.

Lifetime, cancellation and remaining boundaries
------------------------------------------------

Complete exhaustion establishes success, not one batch's ``complete`` bit.
Partially consumed output is not an atomic completed authenticated read.
Close abandoned iterators promptly to release the temporal writer guard;
cooperating writers wait while batches are being consumed. The borrowed native
snapshot is released after locator resolution, on success, error or cancellation.
One source has one active pass; source closure or view/store/serializer/handle
rebinding invalidates an outstanding pass without closing caller resources.

Inherited ``evaluate`` forwards cancellation to extraction and preserves bounded
indexed witnesses. Cancellation checks occur between catalog records, scan rows,
history materializations and canonical iterator steps; locks, native calls and
backend operations are not interruptible deadlines. A failed pass has no new
successful profile and can be retried explicitly when its cause is removed.

The API is temporal-point only, not current-graph/range source admission,
transactional source expansion, persistent planning, publication or a change
to executor defaults. Correct locator extraction is not closure certification
or a permit to publish caller-derived state. #194 measurement/acceptance review
remains distinct from this source implementation.
