# End-to-end extraction benchmark design — #194

Status: executable repeated measurement slice; broader integration remains open. Target:
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
Selection is all rows, one of four predicates, or one predicate+subject.
The 32-byte / variable 32–4096-byte payload is **provenance padding**, not a
4096-byte indexed entity ID. Short IDs avoid violating the durable key limit.
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

Still open: a real authenticated native-claims decoder/locator integration,
variable entity-ID lengths with explicit oversize rejection measurements,
recursive demand/frontier workloads, support discovery/publication accounting,
transactional PyRex native capability expansion, and privileged OS-cold testing
(not required or attempted). This benchmark slice does not close those items or
change the release-readiness defer decisions.
