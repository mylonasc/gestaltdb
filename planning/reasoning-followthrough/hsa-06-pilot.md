# HSA-06 pilot: construction, paired evidence and explicit defer decision

Parent #451; mandatory acceptance #454. Partial acceptance evidence, not roadmap
closure, release authorization or automatic native adoption.

## Production/source boundaries

- #450 (actual-adapter benchmarks/HSA-01) and #461 (HSA-02) are merged to main
  `dcc90e2`. #459 was merged into its stacked base.
- HSA-03 slices #463/#464 and HSA-04 #465/#466 are on `hsa-dev` (`af08f60`),
  **not main or the published v0.16.0 wheel**.
- Paired controller `ab8445a` adds only benchmark/tests. Its library sources
  match HSA-04 `2e5540c`, the source of the 100K construction report. Both are
  identified independently; no old report is relabelled as a new revision.
- A later duplicate-dimension/observation guard changes only acceptance framing;
  recomputing the recorded pilot summaries with that guard gives identical results.

## Preserved 100K dense construction gate

The original HSA-01 report remains unchanged: instrumented construction timed
out at 900 seconds, before any extraction or parity result. New report:

`benchmarks/results/reasoning-454-hsa-06-100k-dense-construction.json`

SHA-256: `97cdf1008892201d2d2c24b9ea30f0ba7eaaad7981a3d284d42daa33b7b487a8`

It constructs **134,286 versions in 35.174s**, preserving the 900s timeout,
100K assertion population, dense amendments, commit batch 1000 and opt-in
instrumentation. Construction passes that gate. Four ordinary observations
also pass exact write-receipt-derived fact/version-ID parity:

| Selection / valid time | Evidence rows | Instrumented pipeline s |
| --- | ---: | ---: |
| all / 30 | 100000 | 54.429 |
| subject / 30 | 1 | 35.945 |
| all / 50 | 85714 | 55.354 |
| subject / 50 | 0 | 35.423 |

Command (report records the exact interpreter/runtime/source/harness hashes):

```bash
uv run --with pyrex-rocksdb --with pyarrow python -m benchmarks.reasoning_locator_pipeline \
  --rows 100000 --commit-batch 1000 --instrument --repeats 1 --methods ordinary \
  --selections all subject --times 30 50 --caches first --timeout 900 \
  --output /tmp/opencode/hsa-06-100k-dense-construction.json
```

This is **not** repeated/unprofiled 100K acceptance or all-time history coverage.
The remaining 35–55s extraction cost matters: the all/50 observation counts
1,448,141 metadata reads and 1,323,003 decode calls over 134,286 stored versions.
Bounded cache pressure and repeated authentication can cause re-decoding. Neither
an immutable history nor a writer guard makes mutable envelope reads optional.

## Unprofiled alternating-pair controller

`benchmarks.reasoning_hsa_acceptance` builds one after-written fixture, then
reopens it in isolated workers importing the requested before/after source trees.
Every worker attests its actual library/harness path. Whole library and harness
hashes/revisions are checked before and after the run; source drift invalidates
the result. A shared fixture means before/after verify **the same version IDs**,
not just independently generated fact cardinalities.

Before/after order alternates each repeat, method order rotates, instrumentation
is disabled, and all individual timing/RSS peaks plus medians/maxima/p95 and
paired ratios are retained. Incomplete/timeout/parity/profile/source-drift results
are atomically preserved **without successful summaries**. At least six pairs
are required for an observed gate; every ratio must be within the limit. Mixed
results are inconclusive, never passes. This conservative rule is not a
statistical-confidence or universal performance guarantee.

Report: `benchmarks/results/reasoning-454-hsa-06-paired-1k.json`

SHA-256: `038adc90d308487540506d6d5ec74a9f80d9d85e1b13dbe9fc126a7d0f04e1f5`

Before `001e276` (HSA-01); after `ab8445a`; 6 alternating pairs per method/
selection/time, first-handle reads, times 30/50. All **216 observations / 108 pairs**
pass exact fact/version parity. All 36 mode-relative latency/RSS observed gates
are within their <=1.05x / <=1.10x bounds against each mode's **own** baseline.
Ordinary improves substantially; its slowdown cannot manufacture a native pass.

Selected time-50 medians and individual maximum RSS:

| Mode / selection | Before s | After s | Before max RSS bytes | After max RSS bytes |
| --- | ---: | ---: | ---: | ---: |
| ordinary / all | 1.439 | 0.126 | 75821056 | 73338880 |
| full-record / all | 2.996 | 0.220 | 107855872 | 104366080 |
| batched locator / all | 2.738 | 0.277 | 107192320 | 104296448 |
| ordinary / subject | 1.431 | 0.101 | 74731520 | 72720384 |
| batched locator / subject | 2.510 | 0.210 | 107397120 | 103989248 |

```bash
uv run --with pyrex-rocksdb --with pyarrow python -m benchmarks.reasoning_hsa_acceptance \
  --before /absolute/path/to/hsa-01-baseline-attribution \
  --after /absolute/path/to/hsa-06-paired-acceptance \
  --rows 1000 --commit-batch 1000 --repeats 6 \
  --methods ordinary native-full-record native-authenticated-locator \
  --selections all predicate subject --times 30 50 --caches first \
  --after-history-batch-size 64 --timeout 900 --output /tmp/opencode/hsa-06-paired-1k.json
```

OS caches remain uncontrolled, hardware/noise are local, and sparse histories,
warm pairs and larger paired scale points are still missing. Profiling reports
from HSA-04 stay separate; their latency is not substituted for these timings.

## Disposition and remaining work

**Defer automatic native adoption.** Every selective locator-vs-ordinary latency
and RSS observed gate breaches the 1.25x target at both times. Paired latency is
about 2.03–2.17x ordinary, despite large improvements against the locator's own
baseline. Full authentication and Arrow/worker memory floors remain material.
Do not weaken checks or raise budgets to manufacture a pass.

**HSA-05: defer the optional new-index/vectorization experiment.** Existing
prefix/range/point batching is measured, but these results do not show that an
additional selective index would remove the dominant authentication and native
memory floors. No new derived format or write/rebuild amplification is justified
by this evidence. Reopen only with a measured residual bottleneck and full
construction/rebuild/index-byte/memory accounting.

Keep the independently revertible shared-path improvements and explicit batching
on hsa-dev for continued acceptance, not a blanket performance promotion. HSA-06
remains open for larger/warm/sparse paired points and final cross-consumer CI/
rollout reconciliation. HSA-03's cache byte accounting is serialized-size based,
not a decoded heap/RSS bound; its broader allocation/lifetime review remains
explicit. The parent is not closed by clearing only the construction blocker.

## Cross-consumer and tracking validation

Final library/controller/tracker source, with all three backends, both binary
serializers, Arrow/Polars, NetworkX, bloom support and Excel checks enabled:

```bash
uv run --with pyrex-rocksdb --with lmdb --with plyvel --with pyarrow \
  --with msgpack --with protobuf --with polars --with openpyxl --with pybloom-live \
  --extra networkx pytest -q
```

Result: **4320 passed, 50 skipped** (remaining optional external capabilities).
This includes current graph/transactions/snapshots, temporal and reasoning
engines, ingestion, Cypher, sampling, source authentication/tamper/cancellation,
native framing/admission, paired-runner failure cases and tracker/workbook tests.
The 15 new controller tests independently cover pair/method order, shared fixture,
loaded-checkout attestations, conservative/noisy gates and incomplete failure
reports. Earlier HSA-04 focused native matrix: 178 passed.

Additional commands pass:

```bash
uv run --extra lint ruff check .
uv run --extra lint python scripts/check_typing.py
uv run python scripts/check_code_quality.py
uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py
uv run --with openpyxl python planning/reasoning-engine/sync_tracker.py --check
uv run python planning/reasoning-engine/check_roadmap.py
uv run python planning/reasoning-engine/check_tracking.py --github
```

The strict typing gate covers 25 modules. Live GitHub tracking matches the
snapshot; the regenerated workbook preserves all 136 feature dispositions and
original acceptance rows. Main-versus-hsa-dev/published-wheel boundaries and
pending #194/#451 work are reconciled rather than promoted to completed features.
