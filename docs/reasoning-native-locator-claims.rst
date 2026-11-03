Authenticated native historical claim locators
===============================================

``reasoning.adapters.native_locator_claims.PinnedNativeLocatorClaimSource`` is
an explicit alternative to the :doc:`reasoning-native-claims` full-record source.
It uses native temporal index-prefix scans and point-materialized versions,
including complete correction/retraction histories, under the same point-view
policy, evidence and logical context contract. Ordinary extraction remains the
default; sources are never selected automatically.

Use plain PyRex with the required native scan capabilities. This API does not
admit transactional handles, current-graph sources or range sources. Install the
Arrow extra and use an initialized managed database with current temporal indexes
and a caller-owned authenticated ``GraphReadView``. Unsupported storage and stale
indexes fail without fallback or repair. A temporal
writer guard protects the complete authenticated pass; it does not turn a raw
backend sequence into authenticated application provenance.

Read a corrected historical claim
----------------------------------

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

Authentication and selection
------------------------------

Locator prefixes narrow candidate selection; they do not replace complete
catalog/world authentication or canonical point-selection checks. The source
reads complete logical histories for candidates before materializing the winning
versions. A selective result can therefore require substantial authentication
and index-transfer work. Native scans are not a general speedup guarantee.

Every pass authenticates the complete pinned commit catalog and expected index
memberships. All native scans borrow one default-family snapshot. Requested
predicate prefixes form a union; world and subject bindings intersect it.
Inclusive valid-start bounds exclude records starting after the query point.
Object bindings, agent/source scopes and derived-claim policy are post-selection
filters, not native expression pushdown. ``profile.pushdown`` describes that split.

Missing, substituted, duplicate or malformed index entries fail before evidence
is returned. Complete candidate histories include corrections and retractions;
removing a retraction locator cannot resurrect an earlier assertion. Winners
must agree with canonical point selection. The unfiltered canonical conformance
check and before/after source verification remain mandatory. Logical context and
exact version-ID evidence match ordinary extraction for the same selection.

``NativeLocatorLimits`` bounds catalog authentication, index rows, point rows
and point bytes independently. These bounds are not a Python heap or RSS limit.
Inspect ``native_profile`` only after successful complete exhaustion; a partially
consumed pass is not a completed authenticated read.

``max_authenticated_records`` caps the full catalog, not matching claims alone.
``max_logical_claims`` bounds catalog histories; ``max_raw_rows`` counts cumulative
dimension/history index rows, including repeated visits. Per-batch limits,
``ClaimSourceLimits`` and request output bounds apply independently. A commit is
authenticated before its length is charged; limits cannot interrupt backend calls.

Pass ``history_batch_size=`` explicitly to use bounded range unions and point
fetches for logical histories. Batching preserves the same authentication and
selection checks; it is not automatic source adoption or a relaxation of limits.
The default ``None`` retains scalar history reads. A supplied size must be between
1 and 65536 and requires actual ``scan_arrow_ranges`` and ``multi_get_arrow``
capabilities (plain PyRex 0.7.1); unsupported requests fail without scalar fallback.

Point requests contain at most ``min(batch_rows, 65536)`` keys. Returned order,
cardinality and decoded versions are checked under the same snapshot.
``max_point_rows`` (default 1,000,000) and ``max_point_bytes`` (default 64 MiB)
count cumulative returned rows and raw key-plus-payload bytes on the batched
path, independently of index and output limits. Byte overflow fails closed.
These counters exclude Arrow overhead, native I/O and RSS.

``NativeLocatorProfile`` distinguishes authenticated records, raw index rows,
candidate IDs, materialized histories, resolved candidates and scan count.
``point_rows``, ``point_bytes`` and ``point_fetches`` describe batched point work
and are zero on the scalar path. Resolved candidates precede policy filtering;
common ``profile.rows`` counts actual output evidence. Do not sum these as a
measurement of total source/engine work.

Authentication reuse
----------------------

Commit decode reuse is scoped to the exact store and entity decoder. New
transaction/snapshot scopes start with empty caches. Closing or replacing the
store, leaving a scope, or failing/interruption during a commit read discards
scoped admission. At most 256 entries and 64 MiB of descriptor, digest and
serialized-record-size accounting are admitted, including the incoming entry.
Oversized commits remain readable but are not cached. This is not a decoded
Python heap or RSS bound; full record envelopes are not retained alongside
decoded versions.

On mutable stores, warm hits reread descriptors, visibility markers and every
record envelope, verify SHA-256 digests and authenticate supersession targets.
Reusing decoded values does not skip those checks. Only verifiable pinned backend
snapshots provide the no-I/O warm path. A temporal horizon or writer lock alone
does not provide that guarantee.

Lifetime and cancellation
---------------------------

Complete exhaustion establishes success, not one batch's ``complete`` bit.
Close abandoned iterators promptly to release the temporal writer guard;
cooperating writers wait while batches are consumed. The borrowed native snapshot
is released after locator resolution, on success, error or cancellation. One
source has one active pass; source closure or view/store/serializer/handle
rebinding invalidates an outstanding pass without closing caller resources.

Inherited ``evaluate`` forwards cancellation to extraction and preserves bounded
indexed witnesses. Checks occur between catalog records, scan rows, history
materializations and canonical iterator steps. Locks, native calls and backend
operations are not interruptible deadlines. A failed pass has no new successful
profile and can be retried explicitly after its cause is removed.

Correct extraction is not closure certification, publication authority or
all-support provenance. Keep source admission, evaluation and publication
separate. See :doc:`reasoning-extraction` for the ordinary batched source and
:doc:`reasoning-executors` for explicit executor selection.
