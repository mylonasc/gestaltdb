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
an empty collection selects none. Request predicates, subjects and objects are
storage-level: they are compared against claim fields before
``ClaimMapping.map`` runs. A demand plan produces logical (post-mapping) names,
so callers must pass it through ``ClaimMapping.storage_predicates`` first —
pushing logical names directly matches nothing at storage level. The claim adapter intersects request
``agents``/``sources`` with ``ClaimSelection``: neither can broaden the other.
``None`` is unrestricted, an empty collection selects none, and ``sources=(None,)``
selects unsourced claims only. Budgets bound yielded logical rows, not RSS, snapshot history
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
   * - ``PyRexStore(transactional=True)`` without native scan entry points (audited 0.5.0)
     - ``pyrex-transaction-snapshot``
     - index + bulk getters
   * - ``PyRexStore(transactional=True)`` with native scan entry points (audited 0.6.0)
     - ``pyrex-native-snapshot`` (raw default-family scan horizon)
     - index + bulk getters + raw ``scan_arrow``; not native claim-source admission
   * - ``PyRexStore`` (default) + ``pyrex-rocksdb>=0.5.0``
     - ``pyrex-native-snapshot``
     - index + bulk getters + ``scan_arrow``
   * - ``PyRexStore`` (default) + older runtime
     - ``pyrex-none``
     - index + bulk getters

LevelDB's multi-database layout exposes no pinned snapshot, so explicit snapshot
requests fail closed there; use LMDB, transactional PyRex or a native-scan-capable
PyRex handle when a pinned horizon is required.
Native snapshot capability describes raw scan lifetime/isolation, not independent
authentication of GraphDB visibility, complete evidence or inference correctness.
The separate :doc:`reasoning-native-claims` adapter still admits only plain PyRex.

Native Arrow scans (``pyrex-rocksdb>=0.5.0``)
--------------------------------------------------

Release 0.5.0 (PyPI, 2026-10-20) implements the requested ``scan_arrow`` /
``Snapshot`` contract on plain non-transactional handles, verified by direct
audit rather than assumed from the proposal:

- ``db.scan_arrow(prefix=, start=, stop=, batch_size=, max_batch_bytes=,
  read_options=, snapshot=, allow_overflow=)`` streams single-consumer Arrow
  RecordBatches with fixed ``key: binary / value: binary`` (non-null) columns;
  empty scans still expose the schema. ``start`` is inclusive, ``stop`` exclusive;
  ``start > stop`` fails upfront with ``ValueError``. ``max_batch_bytes`` counts
  payload plus offset overhead; oversized single records raise unless
  ``allow_overflow=True`` emits them alone.
- ``db.create_snapshot()`` pins a consistent view shareable across scans
  (``scan_arrow(snapshot=snap)``); writes after pinning stay invisible to old
  snapshots while fresh scans see them. Snapshots expose an integer
  ``sequence_number`` and ``is_valid()``; release is idempotent. Foreign snapshots
  fail closed with a native error. ``TransactionDB`` (transactional mode) exposes
  neither entry point in 0.5.0, so native scans on that version require plain handles.
- Re-exporting a stream raises; readers must be consumed once and scans closed
  explicitly. Consumed batches are copied out of Arrow buffers, so rows outlive
  scans, readers, snapshots and database handles.

``gestaltdb.reasoning.adapters.native.NativeKeyScan`` binds this contract:
single-use bounded iteration over caller-chosen key ranges with exact row
accounting, snapshot validity checks before/during/after, owned-snapshot release
on exhaustion, error or close, and ``ReasoningError`` wrapping (native type
chained) so callers never import PyRex exception types. Detection is
capability-based — ``PyRexStore.has_native_arrow_scan()`` plus a
``pyrex-rocksdb>=0.5.0`` version gate — never a version assumption. Decoding raw
KV pairs into facts, mapping predicates and authenticating graph visibility stay
explicit caller/adapter work; this primitive removes Python row processing from
KV extraction, not from payload decoding.

The original 2000-small-row measurement found the Python iterator faster
(~2.4M vs ~1.2M rows/s warm); the native path pays per-batch Arrow overhead that
must not be assumed to disappear at scale. No throughput speedup is claimed —
the value is bounded batches and shared snapshot horizons. Python copying and
payload decoding remain. Subsequent 100K variable-value and authenticated claim
measurements retain a no-adoption decision; see
:doc:`reasoning-frontier-benchmarks` and :doc:`reasoning-native-claims`.
First/primed measurements do not certify an OS-cold cache.

PyRex 0.6.0 upstream re-audit
----------------------------------------

``TransactionDB`` in 0.6.0 adds ``scan_arrow`` and ``create_snapshot`` with the
same default-column-family raw schema/range/snapshot contract. Bounded tests in
``tests/test_reasoning_native_capability_audit.py`` check exact copied rows,
shared snapshots across subsequent writes, ``[start, stop)`` prefix intersections,
owned/borrowed lifetime, single-use scans, row limits, commit/rollback visibility
and isolation of uncommitted transactions. The same test set verifies 0.5.0's
explicit unsupported transactional result, not an implicit fallback.

In 0.6.0 named ``ColumnFamilyHandle`` values still expose neither entry point.
Raw TransactionDB scans read the default family only: a same-prefix key in a
named family does not appear. Passing a named-family handle to the native scan
adapter fails closed; it is never silently coerced to the default family.
No arbitrary column-family routing API is introduced here.

This updates the capability matrix, not source or executor admission.
``PinnedNativeClaimSource`` still rejects transactional handles even when raw
scans work. No temporal/current/range provenance, publication or all-support
guarantee follows from raw snapshot sequence/validity. Installed-runtime gates
remain explicit; these small probes are correctness audits, not performance
observations or a broad future-version guarantee.

Decoded Evidence handoff
----------------------------------------

``iter_decoded_evidence`` turns raw batches into evidence without ever leaking
raw rows into inference: the caller-owned ``decode_row`` maps ``(key, value)``
bytes to a ``DecodedRow`` (fact triple plus exact version/evidence IDs) or
``None`` to skip, while ``predicate``/``bindings`` push down filters before
materialization and every batch carries the caller-authenticated
``visibility`` token. Corrupt rows fail closed with the error chained and no
raw bytes in the message; duplicate version IDs fail closed; budgets bound
the handoff. The ``complete`` flag mirrors the raw batch it was decoded
from — end-of-scan is always generator exhaustion, since filtered-out tails
carry no batch. ``iter_locator_keys`` yields keys only for caller
point-materialization so locator-versus-full-scan tradeoffs can be measured
per backend (column projection would need upstream support and is documented,
not assumed). The 0.5.0 audit found no transactional/named-family scans; the
0.6.0 re-audit above adds raw transactional default-family coverage only.
The GenerationStore question is settled by the scale decision:
no bespoke native kernel — recorded defer.

Batched claim source
----------------------------------------

The underlying claim iterator groups locators by commit and validates complete
touched commits before resolving buffered winners and again on exhaustion.
This avoids a full commit-sized integrity scan per extracted row without
treating a mutable decode cache as trusted storage. Successful consumption of
all batches (including the final verification) is required; discard partial
batches on any error. Row budgets are not a hard bound on the grouped iterator's
locator/winner buffers or on commit-validation work. This does not introduce a
backend snapshot or change LevelDB/PyRex capability guarantees; see
:doc:`temporal-versioning` for the integrity and memory boundaries.

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
claim query; a multi-predicate plan runs one indexed pass per predicate under the same
pinned view and deduplicates by exact version ID. All other selection filters run in
Python, with the actual pushdown mode (``predicate``, ``predicate+subject``,
``predicates``, ``predicates+subject`` or ``none``) recorded in the profile.
``source.profile`` reports batches, rows, pre-filter scanned versions,
verification count and pushdown mode for the most recent completed pass; a new
pass rebuilds it. Row budgets fail closed mid-stream without partial publication;
``evaluate`` reuses the default engine over the flattened batches and returns the
usual context-bound ``ClaimReasoningResult``. Its ``extraction_request`` retains
the normalized request, including the effective agent/source policy. Logical
predicate/subject/object restrictions participate in a versioned context identity;
batch size and row budgets do not. Unrestricted batched contexts remain compatible
with the equivalent unbatched source. Capability access is read-only
metadata: it never snapshots data by itself.

Measurements
----------------------------------------

From a source checkout, compare the existing single-pass iterator with bounded
batched extraction on LMDB and LevelDB, plus the raw Python-iterator vs native
Arrow scan on a throwaway PyRex database (skipped without ``pyrex``/``pyarrow``)::

   uv run --with lmdb --with plyvel --with 'pyrex-rocksdb==0.5.0' --with pyarrow python -m benchmarks.reasoning_extraction --rows 2000 --repeats 2 --output /tmp/opencode/reasoning-extraction.json

The runner asserts identical evidence sets, exact batch accounting, deterministic
repeat passes and fail-closed row budgets, then records rows/s, batch/profile
counters, capability records and full-worker RSS. The committed
``benchmarks/results/reasoning-re05-extraction.json`` (clean source, Python 3.13,
two repeats) reports about 629 vs 612 rows/s (single vs batched) on LMDB and
812 vs 772 rows/s on LevelDB, with eight 256-row batches and five verifications
per pass; the native scan row shows warm Python-iterator vs native throughput on
raw KV pairs at the same scale. Batching adds bounded memory and explicit
verification — not higher throughput. These are small synthetic transfer
observations, not production-scale, proof-mode or incremental-update claims.
Actual source-admitted decoded native extraction and larger corrected measurements
are described in :doc:`reasoning-native-claims`; persistent generation consumers
have their separate admission contracts. This raw primitive does not upgrade them.
