:orphan:

Ontology solver benchmarks and v1 decision
============================================================

This ONTO-07 report evaluates the selected ``gestalt-core-v1`` fragment, not
full OWL reasoning. The implementation contract is :doc:`ontology-contract`.
Benchmark adapters are development tools, not additional public execution
backends. No JVM integration or new runtime dependency is introduced.

Reproduction
------------

Run the standalone module from a source checkout. Optional tools are installed
only in the benchmark environment::

   uv run --with-requirements benchmarks/ontology-requirements.txt python -m benchmarks.ontology --sizes 8 16 --repeats 3 --timeout 10 --temp-root /tmp/opencode --output benchmarks/results/ontology-v1-small.json

``python -m benchmarks ontology`` also dispatches to the runner. The direct
module avoids unrelated imports in the parent CLI; workers always start in fresh
interpreters. On another machine, choose an existing writable ``--temp-root`` or
omit it to use the platform's temporary directory.

To measure an actual wheel's feature payload, first build locally with
``uv build --wheel --out-dir <directory>``, then pass ``--wheel <wheel-path>``.
Building is not publishing. Benchmark results and tools are not packaged into
the runtime wheel.

The other committed matrices use these options with the same pinned environment::

   uv run --with-requirements benchmarks/ontology-requirements.txt python -m benchmarks.ontology --backends native native-proofs clingo owlrl --workloads dense --sizes 64 128 --repeats 3 --timeout 15 --temp-root /tmp/opencode --output benchmarks/results/ontology-v1-dense.json
   uv run --with-requirements benchmarks/ontology-requirements.txt python -m benchmarks.ontology --backends native native-proofs --workloads dense --sizes 128 --repeats 1 --max-candidates 100 --timeout 10 --temp-root /tmp/opencode --output benchmarks/results/ontology-v1-limits.json
   uv run --with-requirements benchmarks/ontology-requirements.txt python -m benchmarks.ontology --backends native clingo owlrl --workloads dense --sizes 64 --repeats 1 --max-facts 100 --timeout 10 --temp-root /tmp/opencode --output benchmarks/results/ontology-v1-preflight.json

Every row records versions/platform context, workload size, resource settings,
stage timings, expected/actual closure digests and process RSS where available.
Report metadata includes the Git revision and exact harness/feature source hashes.
Each completed worker atomically checkpoints the output JSON. ``complete=false``
means the matrix was interrupted; it is not final evidence and the runner does
not claim to resume it automatically. ``complete=true`` means all planned rows
finished, not that every row succeeded: inspect each row's status.

Workloads and correctness
-------------------------

All engines receive the same named-entity propositions. Duplicate evidence is
logically set-valued. Expected closures come from analytical constructions rather
than taking the native engine as the benchmark oracle.

================  ===================  =======================================
Workload          Raw input rows       Expected logical closure
================  ===================  =======================================
``hierarchy``     N                    5N memberships across a five-class chain
``recursive``     N-1                  Direct tree edges plus ancestor pairs
``duplicates``    8N                   N property facts plus 2N memberships
``dense``         N-1                  N(N-1)/2 forward pairs in a chain closure
================  ===================  =======================================

``recursive`` uses a binary parent tree. ``dense`` demonstrates quadratic output
growth; it is not a large-input ingestion benchmark. The shared small matrix
keeps the persistent legacy comparison affordable. Larger solver-only runs must
be reported separately, not silently mixed into that matrix.

Every successful row must match the complete expected fact set and digest.
``unavailable``, ``limited``, ``timeout`` and ``error`` are not fast successful
measurements. Analytical input/output caps run before large fixture allocation.
Native candidate/iteration limits and legacy justification limits remain distinct;
external engines do not implement the native work counter. Per-worker timeouts
apply to all profiles. They are not an exact memory sandbox. The parent owns
temporary directories and cleans them even when killing a timed-out worker.

Profiles and non-equivalent costs
------------------------------------------------------------

* ``native``: immutable facts/evidence, index construction and complete fixed point;
  no explanations or publication.
* ``native-proofs``: the same closure with bounded single-witness storage and one
  separately timed explanation query. This does not enumerate all proofs.
* ``clingo``: compile the same positive program into an integer-interned ternary
  relation through Clingo's Python API. **Grounding can perform closure work**;
  include ``ground_s`` as well as ``execution_s``. Solve time alone is misleading.
* ``owlrl``: build an RDFLib graph and invoke OWL-RL. This computes additional
  vocabulary/schema consequences. Output projection retains only the supported
  individual/property/class facts for validation; computation still pays for
  the broader closure. It supplies no GestaltDB witness trace.
* ``claims-native``: create/load an LMDB temporal source, pin and verify one view,
  extract selected evidence, evaluate the native program, verify again and construct
  a context-bound result. Extraction is explicitly materialized to time it separately;
  this is the staged public-API path, not the streaming wrapper's exact allocation
  profile. ``read_reasoning_s`` excludes database creation and ingestion.
* ``legacy-lmdb``: use existing ``create_rule`` / ``run_rules`` over the same input
  claims. Its execution includes extraction, **all retained justifications** and
  temporal publication; final claim decoding is timed separately. It is not an
  equivalent non-persisting kernel benchmark or an all-else-equal proof comparison.

``compile_s`` measures the common validated ontology frontend for all profiles.
``conversion_s`` measures each backend representation; ``init_s`` and ``load_s``
record setup/ingestion separately. ``pipeline_s`` spans compilation through decoded
output, including initialization and load, but excludes process/import startup,
fixture construction, the separate proof query and validation. It includes minor
instrumentation/temporary-directory overhead. ``worker_wall_s`` additionally
includes interpreter startup and worker/report overhead. Never sum the proof-query
latency into closure time without labelling that different operation.

``backend_import_s`` covers the declared optional packages; profile-specific
GestaltDB/adapter imports may occur within the pipeline. Stage counters therefore
do not necessarily sum to its wall-clock span. ``validation_s`` times actual-result
hashing; expected-hash construction and set comparison are additional worker
overhead, outside the pipeline.

The common import measurement includes existing ``gestaltdb.__init__`` eager
imports; it is not the isolated core's minimal import cost. Separate backend import
costs are recorded. Processes are cold, but filesystem/page caches are not flushed.
Runs are serial, not CPU-affinity-pinned; small timing differences are not meaningful
without more hardware/workloads and repeats. These are synthetic observations,
not an SLA or an absolute speed ranking.

Memory and footprint
--------------------

RSS is the fresh worker's process high-water mark after inference, decode, proof
query and validation. It includes Python, common imports, fixtures, expected sets
and solver allocations, not just the engine. The post-import high-water mark is
also recorded; subtracting two high-water marks is not a precise allocator delta.
``resource.getrusage`` units are normalized for Linux/macOS; unsupported platforms
report ``null``, not a fabricated zero. Legacy LMDB reserves a 512 MiB virtual map;
that reservation is not equivalent to committed RSS.

Feature footprint is the actual ``reasoning`` and ``ontology`` Python source
payload, including the claims adapter. Optional wheel statistics sum feature ZIP
entry sizes; compressed payload excludes ZIP headers/metadata and is not the whole
wheel-size delta. Optional solver dependency sizes sum installed files from an
explicit observed chain (Clingo/CFFI/pycparser or OWL-RL/RDFLib/pyparsing/isodate
when present), including distribution metadata. They are not compressed downloads,
an exhaustive dependency resolver, or universal platform sizes. Existing NumPy/Lark
costs are reported separately, not charged as newly added ontology dependencies.

Measurements and decision
-------------------------

The recorded run used Linux x86-64 (glibc 2.39), CPython 3.13.11, Clingo 5.8.2,
OWL-RL 7.6.2, RDFLib 7.6.0 and LMDB 2.2.1, on 2026-10-19. The host reported
24 logical CPUs; no affinity or CPU isolation was applied. The harness revision
was ``a5f6335``; complete source hashes and resolved dependency versions are in
the reports. The requirements file pins the observed optional tool chain; the
checkout's ``uv.lock`` supplies existing project dependencies.

Committed evidence under ``benchmarks/results/``:

* ``ontology-v1-small.json``: 144 successful rows, all six profiles, four
  workloads, N=8/16, three fresh workers per configuration.
* ``ontology-v1-dense.json``: 24 successful rows, native/native-proofs/Clingo/OWL-RL,
  N=64/128, three fresh workers per configuration.
* ``ontology-v1-limits.json``: two correctly limited native runs at N=128 with
  ``max_candidate_rows=100``; no successful partial closure is reported.
* ``ontology-v1-preflight.json``: three correctly limited analytical preflights
  at N=64 with ``max_facts=100`` (expected closure: 2,016).

All 168 successful rows matched their analytical closures. All four matrices
are complete; the five limited rows are diagnostics, not successful timings.
No decision evidence comes from the earlier interrupted larger legacy run.

Small shared workloads
~~~~~~~~~~~~~~~~~~~~~~

Median pipeline milliseconds at N=16, excluding interpreter/import startup,
fixture construction and the separate explanation query:

.. list-table:: Small workload pipelines (milliseconds)
   :header-rows: 1

   * - Profile
     - Hierarchy
     - Tree recursion
     - Duplicate evidence
     - Dense closure
   * - native
     - 1.15
     - 1.16
     - 1.12
     - 2.36
   * - native-proofs
     - 1.65
     - 1.65
     - 1.58
     - 3.22
   * - clingo
     - 0.85
     - 0.77
     - 0.93
     - 1.00
   * - owlrl
     - 27.82
     - 21.44
     - 16.70
     - 34.21
   * - claims-native
     - 53.89
     - 50.12
     - 1,503.83
     - 51.60
   * - legacy-lmdb
     - 522.77
     - 244.15
     - 2,685.07
     - 1,630.33

These rows have different persistence/proof responsibilities, as defined above;
they are not a single interchangeable-backend speed ranking. Clingo's small-case
differences from native are often sub-millisecond and should not be overstated.
Common GestaltDB import time was approximately 261 ms across profiles; median
additional imports were approximately 4.25 ms for Clingo and 20.65 ms for OWL-RL.
Startup dominates a short-lived process but is normally amortized in an application.

The duplicate case contains 128 evidence rows for 16 distinct property facts.
For ``claims-native``, selected-evidence extraction alone took 1,434.31 ms median,
versus 0.215 ms for its native fixed point. Its post-load read/reason/verify/decode
path took 1,466.31 ms. Switching the kernel cannot solve this source-read bottleneck.
The legacy path additionally decoded persisted assertions/derivations and retained
all justifications. These measurements identify existing temporal API integration
cost, not just ontology rule performance; no claim is made about its asymptotic
cause without further profiling.

Larger solver-only dense closures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table:: Median pipeline milliseconds, no temporal database
   :header-rows: 1

   * - N
     - Closure facts
     - native
     - native-proofs
     - clingo
     - owlrl
   * - 64
     - 2,016
     - 70.11
     - 88.27
     - 10.28
     - 397.39
   * - 128
     - 8,128
     - 537.02
     - 622.88
     - 54.79
     - 2,238.43

Clingo is clearly faster here: approximately 9.8 times lower pipeline latency
than native at N=128. Of its 34.29 ms median grounding-plus-solve cost at N=128,
32.93 ms was grounding; reporting solve time alone would hide most inference work.
The native explanation query was separately approximately 0.145 ms median on that
fixture; storing witnesses was already included in the 622.88 ms pipeline.

Median whole-worker RSS at N=128 was approximately 53.41 MiB native, 54.19 MiB
native-proofs, 58.55 MiB Clingo and 61.92 MiB OWL-RL. These include the substantial
shared package/runtime and benchmark fixtures, not an isolated solver heap.
The benchmark uses a two-million native candidate budget and 1,000 iteration
budget; it does not promise that arbitrary graphs of this size fit default limits.

Observed deployment footprint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The ten new reasoning/ontology Python files, including the claims adapter,
  total **55,337 bytes** (54.04 KiB) of source.
* Their compressed entry payload in the locally built development wheel is
  **16,822 bytes** (16.43 KiB). The complete wheel is 367,119 bytes; it retains
  the checkout's 0.12.1 declaration and is not a newly published release.
* The observed Clingo/CFFI/pycparser installed-file chain totals **7,557,300 bytes**
  (7.21 MiB); OWL-RL/RDFLib/pyparsing totals **3,074,242 bytes** (2.93 MiB) on this
  Python version. Isodate is additionally required on Python 3.10.
* The observed optional LMDB distribution is approximately 0.97 MiB. Existing
  NumPy and Lark costs remain separate; they are not new ontology dependencies.

V1 backend decision
~~~~~~~~~~~~~~~~~~~~

**Keep the native engine as the v1 default, with no mandatory external solver.**
This is a portability, footprint and semantic-contract decision, **not a claim
that native is fastest**. It supplies exact work counters, fail-closed inference,
bounded witnesses and no-site-packages implementation independence in a small
incremental payload. Clingo's benchmark adapter supplies neither the same native
candidate-budget semantics nor those historical witness facilities. Replacing
the production evaluator on timing alone would weaken that contract.

**Clingo is the preferred future optional accelerator candidate** for larger
positive materialization workloads where its native wheel and different resource/
explanation capabilities are acceptable. The Python interface worked across the
tested Python 3.10-3.13 environments; measured performance/RSS here is Linux-only,
not a wheel-availability guarantee for every platform. A production backend needs
an explicit capability/limit contract and explanation design, not silent fallback
or exposure of the benchmark adapter as a supported API.

**Keep OWL-RL as the independent oracle/interoperability candidate**, not the v1
default: its broader semantics and extra RDF representation are useful, but this
fragment does not need the additional closure work. Neither external option makes
authenticated temporal extraction disappear. Prioritize a separately scoped
batch-read/reuse investigation before claiming scalable repeated reasoning over
large claim sets. V1 remains bounded and explicit rather than promising unrestricted
bulk throughput. JVM integrations remain excluded.
