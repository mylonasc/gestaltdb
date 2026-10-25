Authenticated native temporal claim candidates
==============================================

``reasoning.adapters.native_claims.PinnedNativeClaimSource`` is an explicit,
correctness-first native full-record extraction path for a caller-supplied
temporal ``GraphReadView``. It implements the same ``ExtractionRequest``,
``ClaimSelection``, ``ClaimMapping``, ``FactBatch`` and logical ``InputContext``
contract as ``PinnedBatchedClaimSource``. It is not selected automatically.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
   from gestaltdb.reasoning.adapters.native_claims import PinnedNativeClaimSource, NativeClaimLimits
   from gestaltdb.reasoning.extraction import ExtractionRequest

   with TemporaryDirectory() as root:
       graph = GraphDB.create(Path(root) / "db", backend="pyrex", serializer="json")
       try:
           first = graph.assert_claim(subject="alice", predicate="worksFor", object="acme",
               agent="registry", polarity="positive", valid=(0, 100))
           graph.correct_claim(first.logical_id, confidence=0.9, valid=(20, 80),
                               supersedes_version_id=first.version_id)
           with graph.read_view(valid_time=50) as view:
               options = dict(request=ExtractionRequest(world="default", predicates=["worksFor"]),
                   ontology_revision="example-revision", selection=ClaimSelection(), mapping=ClaimMapping())
               with PinnedNativeClaimSource(view, **options,
                       native_limits=NativeClaimLimits(max_raw_rows=1000)) as native:
                   rows = tuple(e for batch in native.iter_batches() for e in batch.evidence)
                   assert len(rows) == 1 and rows[0].reference_id != first.version_id
                   assert native.native_profile.visible_records == 2
               with PinnedBatchedClaimSource(view, **options) as reference:
                   assert rows == tuple(e for batch in reference.iter_batches() for e in batch.evidence)
       finally:
           graph.close()

Admission, selection and identity
---------------------------------

The adapter requires an initialized managed database, plain nontransactional
PyRex, Arrow and the existing native scan runtime capability gate
(``pyrex-rocksdb>=0.5.0``). Unsupported backends or transactional handles fail
explicitly; there is no Python fallback or automatic storage choice. PyRex 0.6.0
exposes scan methods on transactional handles too, but this adapter deliberately
does not admit that distinct source/snapshot path without its own conformance
and capability audit. Merely finding a method is not source authentication.

A pass holds the database temporal writer guard while it verifies the source,
creates the native scan snapshot, validates every row and resolves/filters
evidence. Cooperating temporal writers wait until extraction ends or the
iterator is closed. This is a potentially long-lived lock, not a low-latency
lease. Raw storage mutation outside the writer protocol is not coordinated;
canonical before/after verification still rejects detected corruption.

The raw scan covers all temporal-record kinds and operations through the pinned
commit horizon, not only current positive claims. Every visible native key and
ordinal must match authenticated commit membership and exact serialized bytes.
Coverage counts must match the complete canonical visible commit catalog;
omitted, duplicated, substituted or unordered records fail before evidence is
returned. Unmarked interrupted commits/reservation gaps are not visible input
and are not repaired; any such raw rows within the admitted prefix still count
toward the raw scan budget. An incomplete descriptor that blocks the canonical
contiguous prefix is not silently crossed to expose later commits.
Later commits beyond the pinned prefix do not enter this scan.

Candidate claim histories are collected at the half-open valid-time point.
The existing canonical grouped ``iter_claims_as_of`` point selector resolves
complete logical histories, including corrections and retractions; its result must agree
with the final eligible record in the ordered, fully admitted native history.
Missing/inconsistent logical-index resolution therefore fails closed rather
than omitting native evidence. Only then does the shared batched claim policy
select positive entity claims, intersect agent/source scopes, exclude derived
claims unless requested, apply bindings and map predicates. Independent active
assertion version IDs remain distinct even when their mapped facts coincide.
Negative/literal claims and mutable current-graph records never leak into input.

Physical scan configuration is not logical identity: identical effective
selection/mapping/revision/view/request scopes produce the same context as the
ordinary batched source. Historical views remain historical after appends;
they are not silently upgraded to latest. This API does not add range selection,
typed literal storage admission, durable generation state or publication.

Costs, bounds and consumption
-----------------------------

This implementation uses a **full native record scan plus canonical point
resolution**, not native claim-index locator acceleration. Request filters run
after selection, not as physical predicate/binding pushdown. ``profile.pushdown``
records that boundary explicitly. The raw snapshot still copies bytes from
Arrow to Python and point selection still performs canonical index/storage
reads. Repeated source authentication is retained, not amortized away. This
correctness baseline makes no speedup, hard RSS or scale claim; the existing
benchmark-only assertion decoder is not this API.

``NativeClaimLimits`` bounds raw rows, logical candidate claims, commit horizon,
native batch rows and batch bytes. All record kinds/worlds/polarities/operations
in the scanned prefix can contribute before filtering. Authentication and
canonical index internals may do additional uncounted work. Existing
``ClaimSourceLimits`` and ``ExtractionRequest.max_rows`` independently bound
selected candidates and yielded evidence. An over-budget pass raises and
publishes no successful profile; it never returns a partial result as complete.

``native_profile`` reports raw rows, visible records, logical candidates and
non-retracted resolved claims for a completed pass; common ``profile.rows``
counts actual policy-selected evidence. These counters describe different
stages and are not interchangeable engine-work or memory units.
``iter_batches(cancel=...)`` and inherited ``evaluate(..., cancel=...)`` check
cancellation during native admission and canonical resolution as well as engine
evaluation. A blocked native call, lock wait or backend operation cannot be
interrupted mid-call. Cancellation/bound failures do not poison a subsequent
explicit retry.

Complete exhaustion, not one batch's ``complete`` bit, establishes the completed
authenticated pass and profile. Partial iteration is not an atomic verified
read; close abandoned iterators promptly to release the guard. One source
supports one active pass at a time; closing the source invalidates outstanding
iterators but does not close the caller's view or database. Store, serializer,
native handle and view rebinding are rejected. Inherited evaluation returns a
complete nonpersisting closure with the existing bounded witness contract, not
all-support provenance or a publication permit.
