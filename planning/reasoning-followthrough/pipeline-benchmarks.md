# End-to-end extraction benchmark design — #194

Status: repeated measurement slices plus explicit full-record native claim
admission delivered; broader native locator/support/publication acceptance stays open. Target:
`feature/onto-dev-epic-2`. This scope is the remaining measurement checkbox,
not a takeover of the already merged native-decoding implementation.

## Experiment matrix

- Inputs: 1K, 10K and 100K assertions; retain a bounded-output nonrecursive
  classification workload alongside a separately capped recursive workload.
- Selectivity: all predicates, one predicate, predicate+subject, and a selective
  bound goal whose recursive frontier includes subjects other than the seed.
- Payload sizes: fixed 32-byte and variable 32–4096-byte entity values; stay
  within durable index-key limits and report rejected oversized inputs separately.
- Backends: LMDB, LevelDB capability-appropriate reads, plain PyRex native scans,
  and transactional PyRex only for APIs its installed runtime actually supports.
- Cache labels: fresh process and repeat/warm process are not claims of cold OS
  page cache. Do not drop caches or require privileged operations.

## Stage accounting

Measure data loading separately from source opening/authentication, indexed
locator retrieval, payload decoding, evidence validation, dictionary conversion,
fixpoint evaluation, support discovery, and managed publication. Report P50/P95,
attempted candidates, scanned/accepted rows, exact output cardinality, bytes
copied and fresh-worker peak RSS. Do not compare incompatible `work` counters.

## Gate before expensive runs

A small worker run must compare exact `(fact, version_id)` evidence sets and
closure against the reference iterator. Use generous explicit row/support and
runtime bounds, record failures rather than reporting successful partial runs,
and write reproducible source/runtime/configuration metadata. Raw KV transfer
and `DecodedRow` filtering remain separate experiments until a real authenticated
claim adapter supplies their decoder and source policy. Never label post-decode
filtering as storage pushdown.

## Executable continuation (#194)

`python -m benchmarks.reasoning_matrix` runs the explicit Cartesian product of
sizes/backends/selections/payloads/cache modes, with one isolated worker per
sample. Reports are checkpointed atomically before and after each sample.
Errors/timeouts exit nonzero and have no quantiles. Missing optional runtimes
are `unavailable` (no fallback, `complete=false`, `all_available=false`), not
successful timings; they do not prevent other available cases from running.
P95 is nearest-rank and includes the sample count: three samples are useful
repeatability evidence, not a robust tail-latency characterization.

The claims workload checks exact fact/version pairs against the unbatched
iterator plus independently constructed classification closure. It closes and
reopens the database before extraction; `primed` performs a separate untimed
extraction on that handle first. Neither mode evicts the OS page cache.
Selection is all rows, a predicate matching one quarter of inputs, or one
predicate+subject. The all-row fixture contains only `memberOf`; selective
fixtures contain 25% `memberOf` and 75% unrelated `other` assertions. These are
separately generated workloads, not progressively filtered runs of one database.
The 32-byte / variable 32–4096-byte payload is **provenance padding**, not a
4096-byte indexed entity ID. Short IDs avoid violating the durable key limit.
Padding is deterministic repeated `x` characters and is highly compressible;
these measurements do not characterize incompressible/random payloads.
Earlier reports without padding/reopen are not directly comparable.

The `pyrex-native` experiment is separate synthetic JSON KV data: copied native
Arrow rows through `iter_decoded_evidence`, Python iteration/decoding, and warm
locator enumeration. Both paths compare exact decoded rows on an immutable
dataset with a live native snapshot. Predicate/subject filters run **after
decoding**; only the `row:` prefix is pushed into storage. Locators still copy
values. Raw byte counts describe returned KV payload, not total allocated/copied
memory. The native experiment does not authenticate GestaltDB claims or evaluate
closure. It cannot establish native end-to-end inference acceleration.

Whole-worker peak RSS includes loading, reference validation and oracle closure;
it is not engine-only RSS. Load/warmup/reference time is excluded from reported
claims production stages. Optional runtime versions, exact source revision,
dirty status, harness hashes and every case configuration are retained.

### Reproduction

```sh
# Install selected optional backends and pyrex-rocksdb>=0.5.0 for native scans.
python -m benchmarks.reasoning_matrix --sizes 1000 10000 100000 \
  --backends lmdb pyrex --repeats 3 --output /tmp/opencode/onto-194-ladder.json
python -m benchmarks.reasoning_matrix --sizes 10000 --backends lmdb pyrex \
  --selections all predicate subject --payloads fixed variable \
  --caches first primed --repeats 3 --output /tmp/opencode/onto-194-coverage.json
python -m benchmarks.reasoning_matrix --sizes 1000 10000 100000 \
  --backends pyrex-native --payloads fixed variable --repeats 3 \
  --output /tmp/opencode/onto-194-native.json
```

Subsequently delivered via #366: explicit authenticated full-record native claim
admission with complete raw coverage and canonical historical point conformance.
Its actual pipeline measurements and grouped-read correction are recorded below.
Still open: native claim-index locator admission,
variable entity-ID lengths with explicit oversize rejection measurements,
recursive demand/frontier workloads, support discovery/publication accounting,
transactional PyRex native capability expansion, and privileged OS-cold testing
(not required or attempted). This benchmark slice does not close those items or
change the release-readiness defer decisions.

## Measured evidence — 2026-10-23

Clean source: `4bd5a6d6853303bc16b415c7442c5930bcb8fc83`, based on integration
`54eb114`. Python 3.13.11, LMDB 2.2.1, PyRex 0.5.0, Arrow 24.0.0. Each completed
case has three isolated worker samples with exact parity. Full samples,
configuration, quantiles, runtime versions and hashes are committed under
`benchmarks/results/reasoning-194-*.json`.

### Authenticated reopened-handle ladder

| Assertions | Backend | Extraction median / nearest-rank P95 | Outcome |
| --- | --- | --- | --- |
| 1K | LMDB | 2.342 / 2.351 s | 3 exact-parity samples |
| 1K | PyRex | 4.179 / 4.281 s | 3 exact-parity samples |
| 10K | LMDB | 48.042 / 48.573 s | 3 exact-parity samples |
| 10K | PyRex | 84.215 / 87.288 s | 3 exact-parity samples |
| 100K | LMDB | **not available** | timeout, 300 s whole-worker limit |
| 100K | PyRex | **not available** | timeout, 300 s whole-worker limit |

Both timeouts completed loading, reopening and source authentication, but not
extraction. Their partial phase checkpoints are retained and their quantiles
are empty. The ladder's `complete` flag is **false**. Dataset construction took
18.5/19.2 s in those failed workers, not 300 s of inference. Evaluation in the
completed 10K cases was about 0.11 s. The 10K coverage expansion was not started
after the expensive-run gate failed.

### Bounded coverage and native experiments

- `reasoning-194-coverage-1k.json`: **24 cases / 72 successful samples**, covering
  both backends, all/predicate/subject selections, fixed/variable provenance
  padding and first/primed extraction. Fixed first-read extraction medians were
  2.348/0.593/0.009 s (LMDB) and 4.220/1.029/0.015 s (PyRex) for 1000/250/1
  accepted rows, respectively. Priming did not remove the dominant work.
- `reasoning-194-native.json`: **6 cases / 18 successful samples**, 1K/10K/100K
  synthetic KV rows and both payload sizes. At 100K, native copied Arrow+decode
  medians were 0.276 s fixed / 0.556 s variable, versus Python iterator+decode
  0.241 / 0.516 s. The native path ran first and the reference second, so cache
  order is a confounder, not a fair speed ranking. There is **no demonstrated
  native acceleration**, and these are not authenticated claim timings.

### Follow-up: #314

A separate 1K `cProfile` run found about six million metadata reads over the
whole worker (batched extraction plus unbatched reference verification), mostly
the live-store cached-commit envelope revalidation generator. Profile timings
are not benchmark samples. [#314](https://github.com/mylonasc/gestaltdb/issues/314)
tracks a bounded-read optimization that must preserve the corruption guarantees
from #282. No production cache change or validation bypass is included here.
Earlier reports predate subsequent integration hardening as well as the new
padding/reopen setup; reopening alone has **not** been isolated as the cause.

This slice delivers the reproducible harness and honest bounded/failure evidence,
not all of #194's larger acceptance. Keep #194 open; rerun the larger authenticated
matrix after #314, and retain the native-claims/support/publication deferrals.

### Subsequent grouped-read rerun (#314 / PR #315)

The blocked authenticated size ladder and 10K coverage expansion were rerun on
clean source `49231f3` after commit-grouped reads retained full integrity checks.
All **120 samples** passed exact evidence/closure parity, including 100K fixed
and variable provenance padding on both LMDB and PyRex, first/primed reads.
First-read 100K fixed extraction medians were 2.403/4.207 s (LMDB/PyRex); variable
medians were 2.560/4.806 s. Original timeout reports above remain unchanged.
See [bounded-claim-validation.md](bounded-claim-validation.md) and
`benchmarks/results/reasoning-314-*.json` for full evidence and memory/cache limits.
This unblocks the measured authenticated pipeline, not a native claims adapter;
the remaining #194 integration and workload scopes stay open.

### Actual admitted native source and grouped composition (2026-10-25)

The `native-authenticated` harness method now invokes the production
`PinnedNativeClaimSource`; old `native-decoded`/`native-locator` methods remain
explicitly benchmark-only quiescent assertion paths. Source-contract labels and
last-stage timeout diagnostics prevent accidental equivalence claims.

Baseline clean `c7011bb`: 15 parity successes plus one retained 100K native
timeout after 120s in extraction. The per-claim canonical point composition
took ~3.29s at 1K and 35.46s at 10K, not native scan throughput. Corrected clean
`1730fb7`: one existing grouped canonical point iterator per pass, preserving
native complete-coverage/history conformance and canonical integrity checks;
**40/40 successful evidence/full analytical closure parity observations** across
the staged ladder and repeated 100K selective/sparse first/primed comparisons.
Both source revisions share harness `220d7fc7`; baseline failures are immutable.

The repeated 100K admitted-native total pipeline medians are 16.725/9.589s
selective first/primed and 17.189/10.555s sparse, versus ordinary
10.756/7.402s and 11.609/8.361s. Worker RSS medians ~992--1020 MB native versus
634--662 MB ordinary include priming and parity validation. No native adoption
is justified. This is PyRex 0.6.0, variable bounded IDs, compressible provenance,
uncontrolled OS cache, fixed method order and three-repeat descriptive evidence,
not independent OS-cold/scale/durability certification or all-support provenance.
Full reports and reproduction/limits: `docs/reasoning-native-claims.rst`,
`benchmarks/results/reasoning-re194-{admitted,grouped}-native-*.json`.

Still separate: authenticated native index-locator source admission (the actual
index-locator benchmark is not a general source), transactional native source
capability expansion, oversize rejection measurements and support/publication
stage accounting. Existing recursive/full-closure parity does not measure all
these stages. Preserve #194 open until original acceptance and the explicit
measurement register are reconciled; no automatic adoption/main/release action.
