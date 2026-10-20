:orphan:

Pinned batched claim extraction
===============================

``gestaltdb.reasoning.extraction`` defines storage-neutral bounded extraction
contracts; ``gestaltdb.reasoning.adapters.batched.PinnedBatchedClaimSource``
binds them to an explicitly supplied temporal read view. The core module is
standard-library-only and never touches storage, clocks or network state. The
adapter borrows the caller's view and graph and never opens secondary handles,
initializes identity, rebuilds indexes or publishes claims.

Requests, batches and capabilities
----------------------------------------

.. code-block:: python

   from gestaltdb.reasoning.extraction import ExtractionCapability, ExtractionRequest, batch_evidence

   request = ExtractionRequest(world="default", predicates=["knows"], subject="alice",
                               batch_rows=256, max_rows=100_000)
   assert request.predicates == ("knows",)
   assert not ExtractionCapability.probe(object()).pinned_snapshot
   try:
       ExtractionCapability.probe(object()).require_pinned_snapshot()
   except ValueError as error:
       assert "no pinned read snapshot" in str(error)

``ExtractionRequest`` carries predicate/binding/world selection plus explicit
``batch_rows``/``max_rows`` budgets. ``predicates=None`` selects all predicates;
an empty collection selects none. ``agents``/``sources`` are adapter-enforced
selection hints. Budgets bound yielded logical rows, not RSS, snapshot history
or wall time. ``batch_evidence`` splits any evidence stream into immutable
``FactBatch`` values with zero-based indexes and a ``complete`` flag on the final
batch only. It consumes at most one extra input to detect overflow or
termination; source errors propagate and no partial batch is yielded on failure.

``ExtractionCapability.probe`` describes a store object through class attributes
only — backend name, snapshot kind, pinned/verifiable snapshot support,
predicate-index and multiget availability, and ``native_arrow_scan``. Probing
performs no I/O and opens no handles. Unknown backends fail closed to an
all-False record; ``require_pinned_snapshot`` rejects unsupported snapshot
requests with the responsible backend and kind named, instead of silently
reading without a pinned horizon.

Probed capability matrix (``main`` storage backends):

.. list-table:: Probed backend capabilities
   :header-rows: 1

   * - Backend
     - Snapshot
     - Predicate / multiget
   * - ``LMDBStore``
     - ``lmdb-read-transaction``
     - index + bulk getters
   * - ``LevelDBStore``
     - ``leveldb-none``
     - index + bulk getters
   * - ``PyRexStore(transactional=True)``
     - ``pyrex-transaction-snapshot``
     - index + bulk getters
   * - ``PyRexStore`` (default)
     - ``pyrex-none``
     - index + bulk getters

LevelDB's multi-database layout exposes no pinned snapshot, so explicit snapshot
requests fail closed there; use LMDB or transactional PyRex when a pinned horizon
is required. ``native_arrow_scan`` is False on every backend today: PyPI still
serves ``pyrex-rocksdb 0.4.1`` and the public PyRex branch exposes no ``scan_arrow``
(or ``arrow_c_stream``/``read_columnar``/``arrow_scan``) entry point. Raw Arrow KV
reads remain optional upstream work (PyRex #31), not an assumed API. Enabling them
later requires pinning the released version, validating the C Stream/ownership/
batch-limit/snapshot contracts, and mapping raw KV ranges to decoded facts with
exact evidence IDs and authenticated visibility — application work that stays in
GestaltDB adapters, never in PyRex.

Batched claim source
----------------------------------------

.. code-block:: python

   from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
   from gestaltdb.reasoning.extraction import ExtractionRequest

   # ``view`` is a caller-managed temporal GraphReadView; ``selection`` and
   # ``mapping`` are the usual explicit claim policy values.
   request = ExtractionRequest(world="default", batch_rows=256, max_rows=100_000)
   # with PinnedBatchedClaimSource(view, request=request, ontology_revision=rev,
   #                               selection=selection, mapping=mapping) as source:
   #     for batch in source.iter_batches():
   #         ...  # bounded Evidence tuples with exact version-ID references
   #     profile = source.profile

The source verifies snapshot authentication before the first batch, re-checks the
view binding per record, and verifies again after the final batch. Closing the
source invalidates its iterators, never the caller's view or graph. A single
predicate — optionally with a subject binding — is pushed to the existing indexed
claim query; all other selection filters in Python, with the actual pushdown mode
(``predicate``, ``predicate+subject`` or ``none``) recorded in the profile.
``source.profile`` reports batches, rows, pre-filter scanned versions,
verification count and pushdown mode for the most recent completed pass; a new
pass rebuilds it. Row budgets fail closed mid-stream without partial publication;
``evaluate`` reuses the default engine over the flattened batches and returns the
usual context-bound ``ClaimReasoningResult``. Capability access is read-only
metadata: it never snapshots data by itself.

Measurements
----------------------------------------

From a source checkout, compare the existing single-pass iterator with bounded
batched extraction on LMDB and LevelDB::

   uv run --with lmdb --with plyvel python -m benchmarks.reasoning_extraction --rows 2000 --repeats 2 --output /tmp/opencode/reasoning-extraction.json

The runner asserts identical evidence sets, exact batch accounting, deterministic
repeat passes and fail-closed row budgets, then records rows/s, batch/profile
counters, capability records and full-worker RSS. The committed
``benchmarks/results/reasoning-re05-extraction.json`` (clean source ``b320c33``,
Python 3.13, two repeats per backend, 2000 claims) reports about 629 vs 612
rows/s (single vs batched) on LMDB and 812 vs 772 rows/s on LevelDB, with eight
256-row batches and five verifications per pass. Batching adds bounded memory
and explicit verification — not higher throughput. These are small synthetic
transfer observations, not production-scale, proof-mode or incremental-update
claims. Persistent generation maintenance and native scan acceleration remain
later roadmap work.
