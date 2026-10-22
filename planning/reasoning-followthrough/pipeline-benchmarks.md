# End-to-end extraction benchmark design — #194

Status: initial measurement design; no 100K throughput claim. Target:
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
