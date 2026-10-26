Authenticated native temporal claim candidates
==============================================

``reasoning.adapters.native_claims.PinnedNativeClaimSource`` is an explicit,
correctness-first native full-record extraction path for a caller-supplied
temporal ``GraphReadView``. It implements the same ``ExtractionRequest``,
``ClaimSelection``, ``ClaimMapping``, ``FactBatch`` and logical ``InputContext``
contract as ``PinnedBatchedClaimSource``. It is not selected automatically.
For the separately explicit historical index-prefix source with retained full
catalog authentication, see :doc:`reasoning-native-locator-claims`. This page
and its measured results describe the full-record source only.

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
           first = graph.epistemic.assert_claim(subject="alice", predicate="worksFor", object="acme",
               agent="registry", polarity="positive", valid=(0, 100))
           graph.epistemic.correct_claim(first.logical_id, confidence=0.9, valid=(20, 80),
                               supersedes_version_id=first.version_id)
           with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
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
correctness baseline makes no native speedup or hard RSS claim; the existing
benchmark-only assertion decoder is not this API. The actual admitted-source
measurements below retain the cost of canonical conformance rather than
presenting the raw Arrow transfer as the whole pipeline.

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

Measured grouped-read continuation (#194)
------------------------------------------

``benchmarks.reasoning_frontier_pipeline`` explicitly names this path
``native-authenticated``, separately from the older benchmark-only
``native-decoded`` / ``native-locator`` assertion-fixture shortcuts. Each success
labels its ``source_contract`` and includes separate native raw/visible/logical/
resolved row counters. Timeouts/errors preserve the last flushed worker stage;
that stage is diagnostic, never a successful partial timing or result.

The baseline reports ``benchmarks/results/reasoning-re194-admitted-native-*.json``
bind clean source ``c7011bbe2957903678b5b88e5513403bf9e682ad``. They retain 12
parity successes at 1K first/primed (three repeats), two at the single-sample 10K
first gate, and one 100K reference success plus a **120-second native extraction
timeout**. Native extraction medians at 1K were 3.290 / 3.269s first/primed,
versus reference 0.055 / 0.054s. The 10K native sample took 35.462s versus
0.430s reference. No native 100K baseline timing or closure result exists.

The correction composes native admission with **one existing grouped canonical
point iterator**, not one point operation per logical ID. This retains canonical
commit-group validation and exhaustion checks, exact native-history winner
conformance, complete raw coverage and before/after source verification. Tests
reject missing, duplicate, stale or malformed canonical results and cap metadata
reads on 100/200-row single commits. No source integrity check was disabled.

Five grouped reports, ``reasoning-re194-grouped-native-*.json``, bind clean
source ``1730fb79d9b1e51eb5e245a3e92434db718a9c32`` and retain **40/40 exact
evidence/analytical complete-closure parity successes**. Both baseline and
corrected runs share harness SHA256
``220d7fc711eed1b79f1db07d7cfb04c9144fd12d5ced661f6e8b618d02f331d5``;
the native-source implementation changed, not the harness. Gates repeat the
1K/10K/100K ladder, then separately run three repetitions per first/primed/method
for 100K selective and sparse fixtures. Gate runs with one sample are not tail
estimates. The repeated 100K closures contain 200028 / 300019 facts respectively.

.. list-table:: Repeated 100K total pipeline seconds: median / nearest-rank P95
   :header-rows: 1

   * - Fixture/cache
     - Ordinary indexed source
     - Admitted native source
   * - Selective first
     - 10.756 / 10.979
     - 16.725 / 17.005
   * - Selective primed
     - 7.402 / 7.537
     - 9.589 / 9.602
   * - Sparse first
     - 11.609 / 11.851
     - 17.189 / 17.251
   * - Sparse primed
     - 8.361 / 8.470
     - 10.555 / 10.693

Both methods evaluate through the unchanged indexed Python engine; the measured
difference is source admission/composition, not a new inference engine.
Grouped native extraction medians were 10.7--10.9s first / 7.1--7.4s primed,
versus ordinary 5.1s. Worker high-water RSS group medians were roughly
992--1020 decimal MB native versus 634--662 MB ordinary. RSS includes priming,
mapped pages and parity validation, not isolated query allocations. Copied bytes
remain uninstrumented, not zero. Native row counters exclude canonical index and
authentication work, so do not treat them as total work or memory accounting.

These serial PyRex-only fixtures use variable bounded entity IDs, compressible
32--288-character provenance padding and 1000-write commits. Reports retain full
runtime/platform/thread metadata (Python 3.13.11, PyRex 0.6.0, Arrow 24.0.0;
Polars/OMP configured to one). First means reopened handle, **not OS-cold**;
method order is fixed, cache/host effects remain uncontrolled, and three-sample
P95 is only the maximum. Ingestion, priming and parity checking are excluded
from total pipeline time, with separate load/worker-wall metadata retained.

**Decision: no native adoption.** Grouping removes the per-claim composition
bottleneck and permits successful 100K reads while preserving integrity, but
the admitted native full scan is slower and uses more worker RSS than ordinary
extraction here. Prior restricted native decoder results are not transferable
to this source. Native index-locator admission, transactional source expansion,
support/publication stage accounting and broader #194 acceptance remain separate.

Reproduce from a clean committed tree, writing output outside the checkout:

.. code-block:: console

   uv run --extra backends --extra arrow --with 'pyrex-rocksdb>=0.5.0' python -m benchmarks.reasoning_frontier_pipeline --rows 100000 --shape selective --backend pyrex --methods indexed-python native-authenticated --caches first warm --repeats 3 --timeout 120 --output /tmp/opencode/native-admitted-selective.json
