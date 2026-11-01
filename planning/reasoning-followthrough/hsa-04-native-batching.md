# HSA-04 second slice: explicit native logical-history batching

Parent #451; implementation #455; source `9fc1059`. Built on hsa-dev after #465.

## Implementation and admission

`PinnedNativeLocatorClaimSource(..., history_batch_size=64)` opts into:

1. Bounded groups of complete logical-history raw-key ranges, using the actual
   plain-PyRex `scan_arrow_ranges(ranges=[(start, stop), ...], snapshot=...)` API.
2. Required-record Arrow point fetches using `multi_get_arrow(keys=...,
   snapshot=...)` under the **same native snapshot**. Each request has at most
   `min(batch_rows, 65536)` keys. Returned order/cardinality and exact decoded
   authenticated versions must match; missing values fail closed.
3. Independent cumulative `max_point_rows` and raw key-plus-payload
   `max_point_bytes` limits, alongside unchanged catalog/index/candidate/output
   budgets. Native calls are cooperative boundaries, not interruptible RSS caps.

The default `history_batch_size=None` retains scalar history scans. Explicit
unsupported requests never fall back. Raw readers share NativeKeyScan's existing
snapshot ownership, Arrow framing and cleanup code; new capability seams are in
the strict typing ratchet. No on-disk format, digest, output identity, publication
authority or ordinary/default source change.

Full catalog authentication, the broad world scan and independent full grouped
canonical conformance remain. Native snapshots do not authenticate live canonical
history: a test mutates the live record after pinning native reads and verifies
that canonical authentication still rejects it before evidence.

## Actual runtime contracts tested

Against `pyrex-rocksdb==0.7.1`:

- Half-open range pairs, unsorted/overlapping/duplicate/empty ranges, native
  sorted union deduplication, non-ASCII and separator/zero bytes.
- Arrow point fetch input order/duplicates; null missing values fail closed.
- Tiny Arrow batches, cumulative rows, byte overflow, single use, owned/borrowed
  pins, early close, invalidated snapshots and foreign handles.
- Source correction/retraction/half-open points, policy/predicate/subject/world
  parity, old horizons after append and missing-retraction detection.
- Late missing/duplicated/reordered/substituted native rows, no first evidence
  or completed profile on failure; cancellation closes both active streams and
  releases the shared pin; full canonical omissions/duplicates/old winners fail.
- Index rows still count each physical index visit across chunks. Point rows,
  bytes and calls are separate: `scans` does not hide point fetches.

## Diagnostic measurements

Four immutable reports in `benchmarks/results/`, same source/runtime/harness:

| Report suffix | SHA-256 |
| --- | --- |
| `1k-scalar.json` | `60508f62773d088c27b549969e0f8d802b1450f72c3d3cfa049d3833676a934d` |
| `1k-b64.json` | `8d9f626328306f2c5db85de99469573b286d0a8fefd25fd18fa5f3a46d11a46f` |
| `10k-scalar.json` | `81b99ee75e92096a44825f10660a884ac15668c22480cd3cdf0ee3774c894f40` |
| `10k-b64.json` | `fec23b5a64dfd2485dc0df494e84ee0b680ae6f8592bff1115a3f36ac8983fb0` |

Every filename starts with `reasoning-455-hsa-04-batching-`. All 108 extraction
observations pass exact write-receipt-derived fact/version-ID parity. 1K covers
times 30/50, 10K covers time 50; both cover all/predicate/subject, first/warm and
three repeats. Selected time-50 first-read medians (instrumentation enabled):

| Scale/selection | Scalar index scans | B64 index scans | B64 point calls | Scalar p50 s | B64 p50 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1K all | 1001 | 17 | 16 | 0.335 | 0.296 |
| 1K predicate | 252 | 6 | 4 | 0.262 | 0.248 |
| 1K subject | 4 | 4 | 1 | 0.236 | 0.231 |
| 10K all | 10001 | 158 | 157 | 3.003 | 2.581 |
| 10K predicate | 2502 | 42 | 40 | 2.222 | 2.099 |
| 10K subject | 4 | 4 | 1 | 1.945 | 1.928 |

10K all keeps 25,429 index rows and 13,429 materialized records. Scalar point
metadata reads move to Arrow point rows; they have not disappeared. B64 point
transfer is 13,394,150 raw key/value bytes. First-read whole-worker median RSS is
184,418,304 → 184,221,696 bytes (not a significant memory claim). Every individual
RSS peak and p50/p95 is retained in raw reports; warm worker peaks include warmup.

### Exact commands

Run scalar and b64 separately from the source checkout:

```bash
uv run --with pyrex-rocksdb --with pyarrow python -m benchmarks.reasoning_locator_pipeline \
  --rows 1000 --commit-batch 1000 --instrument --repeats 3 \
  --methods native-authenticated-locator --selections all predicate subject \
  --times 30 50 --caches first warm --output /tmp/opencode/scalar.json
```

For b64 add `--locator-history-batch-size 64`. For 10K use `--rows 10000 --times 50`.
Reports record exact commands, source/harness hashes, configuration and versions.

### Limits and disposition

This is profiling/engineering evidence, **not final HSA-06 acceptance**: profiling
was enabled, OS caches are uncontrolled, scalar reports preceded b64 reports,
method order did not alternate, three repeats are not a latency distribution,
and no ordinary-relative adoption goal is established. Broad authentication
still dominates selective requests. Keep batching opt-in; do not add a compound
index/derived format solely from these results. HSA-05 remains measurement-gated.

Next: final-source cross-consumer/backend/serializer checks, preserved 900s 100K
dense construction gate, and isolated alternating paired runs with profiling
separate for HSA-06. Reject/defer reproducible regressions; never raise default
budgets or weaken admission to manufacture a pass.
