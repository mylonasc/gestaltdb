Authenticated native historical claim locators
===============================================

``reasoning.adapters.native_locator_claims.PinnedNativeLocatorClaimSource`` is
an explicit alternative to the :doc:`reasoning-native-claims` full-record source.
It uses native temporal index-prefix scans and point-materialized versions,
including complete correction/retraction histories, under the same point-view
policy, evidence and logical context contract. Ordinary extraction and the
full-record native source remain unchanged; there is no automatic selection.

Authentication reuse and cold verification (HSA-03)
--------------------------------------------------

Commit decode reuse is scoped to the exact store and entity decoder. New
transaction/snapshot scopes start with empty caches; close, store replacement,
scope exit and failed/interrupted commit reads discard scoped admission. At
most 256 entries and 64 MiB of descriptor plus serialized-record-size accounting
are admitted, including the incoming entry. Oversized commits remain readable
but are not cached. This accounting is **not** a decoded Python heap or RSS
bound. Full record envelopes are not retained alongside decoded versions.

On mutable stores, warm hits still reread the descriptor, visibility marker
and every record envelope, verify existing SHA-256 digests, and authenticate
supersession targets. Reusing decoded values does not authorize skipping those
checks. Work remains linear in the authenticated envelopes (including target
commits); cold reads additionally decode them. Only verifiable pinned backend
snapshots retain the existing no-I/O warm path. A temporal horizon or writer
lock alone does not supply that guarantee. No new digest, persistent checkpoint,
on-disk format or publication authority is introduced.

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
   uv run --extra rocksdb --extra arrow python -m benchmarks.reasoning_locator_pipeline --rows 1000 --commit-batch 250 --instrument --times 30 50 --repeats 3 --output /tmp/opencode/locator-attributed.json
   uv run --extra rocksdb --extra arrow python -m benchmarks.reasoning_locator_pipeline --rows 100000 --times 30 50 --repeats 3 --timeout 600 --output /tmp/opencode/locator-100k.json

For a separately labelled sparse-history scale study, add
``--history-rows 1000`` to apply corrections/retractions only to the first 1,000
assertions while retaining all 100,000 assertions in authentication and canonical
conformance. The default still applies history to the whole fixture. Reports
record the requested density and the fixture's actual historical version count;
sparse-history results must not be relabelled dense-history measurements. Fixture
construction can itself time out before extraction; such a failure supplies no
extraction timing, parity result or worker RSS.

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

HSA-01 opt-in attribution and committed baselines
-------------------------------------------------

HSA-01 adds **benchmark-only attribution** behind ``--instrument``. It does not
change production code, default budgets, verification, historical winners or
evidence. Wrappers are installed only for the selected benchmark process and
cover every concrete store class in ``gestaltdb.kvstores``, including
transaction-bound children, so counters are not limited to the parent facade.
Ordinary extraction remains the default and no optimization or adoption claim is
made here; HSA-02 and later work own remediation.

The opt-in counters distinguish:

* physical commit-descriptor, visibility-marker, record-envelope, sequence and
  index-state metadata reads/writes, by concrete store scope and category;
* exact/range index scan openings and rows, native scan openings/batches/rows and
  native preparation/transfer time;
* commit authentication, version decoding, supersession-target fallback passes,
  commit visits per fallback, distinct target version IDs and retained commit-cache
  entries;
* visibility-checksum calls and marker visits;
* construction commit, validation-target, retraction-validation, sequence
  reservation, index writing/publication and stale-mark phases.

``--commit-batch`` controls assertion records per fixture commit independently of
row count; a batch larger than the row count is a single commit. Fixture
construction emits flushed ``PIPELINE_PROGRESS`` checkpoints for assertion,
correction, retraction, oracle, commit/validation start, sequence-reservation,
index-write, publication and completion phases. A timed-out construction worker
therefore retains its last durable stage. ``fixture.construction`` contains stage
timings, while reports add ``fixture_attribution`` and an extraction
``attribution`` summary. Attribution
timings are inclusive and non-exclusive: nested work contributes to more than one
bucket and does not sum to end-to-end time. Counter presence is not an RSS,
backend-read or elapsed-time guarantee.

Committed HSA-01 baseline reports live under
``benchmarks/results/reasoning-457-hsa-01-*`` and retain their exact command,
source revision, harness hashes and measured configuration. They are single
observations except where repeats are stated; OS caches were uncontrolled and
different runs are not a controlled scaling curve.

.. list-table:: Committed HSA-01 baselines (source 94d53d3)
   :header-rows: 1

   * - Report
     - Rows / history
     - Commit batch
     - Fixture load s
     - Status
   * - ``1k-dense-b1000``
     - 1,000 / 1,000
     - 1,000
     - 1.37
     - complete
   * - ``1k-dense-b250``
     - 1,000 / 1,000
     - 250
     - 1.14
     - complete
   * - ``1k-dense-r3``
     - 1,000 / 1,000
     - 1,000
     - 1.30
     - complete, 3 repeats
   * - ``1k-sparse``
     - 1,000 / 100
     - 1,000
     - 0.35
     - complete
   * - ``10k-dense``
     - 10,000 / 10,000
     - 1,000
     - 86.81
     - complete
   * - ``10k-sparse``
     - 10,000 / 1,000
     - 1,000
     - 3.12
     - complete
   * - ``100k-sparse``
     - 100,000 / 1,000
     - 1,000
     - 22.66
     - complete
   * - ``100k-dense-timeout``
     - 100,000 / 100,000
     - 1,000
     - not reached
     - fixture timeout at 900 s, last stage ``fixture-commit``

The first ``1k-dense-b1000`` report is clean. Later reports record ``dirty`` true
because earlier result files were still untracked when they were measured. Every
recorded harness hash matches revision ``94d53d3``, so the measured harness code
is identical; preserve the qualification rather than relabelling them
clean-source evidence. The companion
``benchmarks/results/reasoning-457-hsa-01-baseline-index.json`` is a compact,
checksummed index of every HSA-01 report, its command, fixture stages and a
representative attribution group.

The ``1k-dense-r3`` report repeats the three sources three times for the
``all`` selection at valid times 30 and 50. At time 50 the authenticated pipeline
p50/p95 was 1.89/2.03 seconds ordinary, 4.02/4.09 seconds full-record native and
3.40/3.67 seconds authenticated locator. The physical counters were identical
across repeats because work is deterministic; that is not a claim that latency
is deterministic. These small repeated samples are a local noise check, not a
general latency distribution.

Dense history dominates construction. For the 10K dense fixture, 1.91 seconds of
assertion commits were followed by 48.47 seconds of corrections and 36.22 seconds
of retractions. Construction performed 48.67 million metadata reads, of which
48.44 million were record envelopes, and resolved 3,429 distinct supersession
targets across 6,858 fallback passes. The 10K sparse fixture with the same
assertion count but amendments only in the first 1,000 rows constructed several
orders of magnitude more cheaply, and its extraction did not repeat the dense
target fallback work. A matched 1K fixture loaded in 0.35 seconds with sparse
history (100 amended rows) versus 1.37 seconds with full dense history.

Extraction attribution separates transfer from authentication. At 10K dense,
valid time 50, ``all`` selection produced approximately:

.. list-table:: Single observations at 10K dense, valid time 50
   :header-rows: 1

   * - Source
     - Pipeline s
     - Metadata reads
     - Commit auth calls
     - Target fallbacks
     - Native rows
   * - Ordinary
     - 31.14
     - 13,340,758
     - 28,523
     - 1,001
     - 0
   * - Native full-record
     - 151.89
     - 67,693,694
     - 140,776
     - 8,288
     - 13,429
   * - Authenticated locator
     - 99.40
     - 43,370,488
     - 90,744
     - 4,859
     - 25,429

These counts identify where amplification sits; they are not an allocation
profile or a claim that a particular fix is sufficient. The dense-history 100K
construction attempt retains the existing 900-second timeout and is recorded as
a failed construction gate, not as extraction, RSS or quantile evidence.

Measured decision: defer automatic adoption
-------------------------------------------

The actual-adapter 100K **sparse-history** gate completed with **18/18 exact
fact/version-ID parity samples**: three sources, three selections, valid times
30 and 50, first extraction on reopened handles, one observation per case.
Source was clean ``48b7d5306c9a369e521c768e8d848331f6621139`` at launch;
Python 3.13.11, pyrex-rocksdb 0.7.1 and PyArrow 24.0.0. The committed report is
``benchmarks/results/reasoning-194-locator-100k-sparse-history-gate.json``.

This fixture uses ``--history-rows 1000``: 100,000 assertions and 343 additional
correction/retraction versions. Fixture loading took 21.86 seconds in its separate
worker. All 100,343 versions remain subject to catalog authentication. Observed
authenticated pipeline times and whole-worker peak RSS at valid time 30:

.. list-table:: Single observations, not repeated latency estimates
   :header-rows: 1

   * - Selection
     - Ordinary s / MiB
     - Full-record native s / MiB
     - Authenticated locator s / MiB
   * - Subject (one evidence row)
     - 5.328 / 425.1
     - 15.167 / 800.7
     - 14.521 / 832.9
   * - Predicate (25,000 evidence rows)
     - 6.588 / 438.9
     - 15.226 / 800.2
     - 16.747 / 834.0
   * - All (100,000 evidence rows)
     - 8.820 / 489.9
     - 15.872 / 823.1
     - 23.604 / 840.0

At valid time 50, the subject is retracted and yields zero evidence rows; the
locator still takes 14.687 seconds versus 5.274 ordinary. Predicate/all requests
return 24,964/99,857 evidence rows with exact historical version parity. Across
the six selection/time pairs the locator takes approximately 2.5--2.8 times the
ordinary pipeline time in these observations, with higher whole-worker RSS.

For the single-subject request at time 30, the locator authenticates 100,343
records and transfers 125,254 index rows to identify one candidate and materialize
two records in four scans. Predicate selection opens 25,002 scans for 25,000
candidates. Selective materialization does not eliminate full authentication,
broad world-prefix transfer or canonical conformance; many-candidate workloads
also pay per-history scan overhead. These counters describe observed work, not
an exact allocation attribution or proof of every bottleneck.

The separate **dense-history** 100K fixture attempt timed out during construction
at 900 seconds, before extraction ran. Its clean source was ``4c3d125``. Preserve
``benchmarks/results/reasoning-194-locator-100k-dense-fixture-timeout.json`` as a
failed construction gate, not a native/ordinary extraction timing or RSS result.
The sparse-history success does not resolve that dense-history performance debt.

The separate dense-history **10K** matrix also completed: **36/36 exact evidence
parity samples**, covering the three sources/selections, times 30/50 and first/
primed reads, one observation per case. Its 13,429-version fixture loaded in
66.96 seconds. At time 30, subject pipeline times were 24.196 seconds ordinary,
122.918 full-record native and 81.591 authenticated locator; primed times were
23.772, 122.156 and 77.211 seconds respectively. Correct output does not remove
the dense-history authentication cost, even for a single subject.

This diagnostic report is preserved separately as
``benchmarks/results/reasoning-194-locator-10k-dense-history-gate.json``. Its parent
captured clean ``4c3d125`` and the harness hash at launch; additive sparse-fixture
and optional-metadata changes were committed while it was running. Those changes
left its default fixture behavior and measured worker bodies unchanged, but later
workers could import the updated module. Preserve that qualification: the report
is not wholly immutable-source release evidence, and its recorded clean-at-launch
flag must not be interpreted as a guarantee across the run. Dense 10K and sparse
100K are different workloads, not a same-shape scaling comparison. First/primed
are cache states, not independent repeated samples of one state.

**Decision: defer automatic native locator adoption.** Ordinary extraction remains
the default; retain the authenticated locator as an explicit correctness-preserving
alternative. Reject a speedup/default-switch claim for this evidence, not the API's
historical semantics. Targeted remediation and measurable goals are tracked in
`technical-debt issue #451 <https://github.com/mylonasc/gestaltdb/issues/451>`_.

Limitations: these are single observations, not three-repeat median/P95 estimates;
one-element summary quantiles in the JSON do not establish a distribution. OS
caches and host contention were uncontrolled, including overlap with another gate.
RSS is whole-worker high-water, not query-only memory. Padding is compressible;
copied bytes are not instrumented. These are extraction/authentication results,
not inference, support, publication, transactional source or OS-cold-cache evidence.
Repeated isolated measurements are required before any future adoption claim;
none is needed to retain the current default and defer optimization to #451.

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
