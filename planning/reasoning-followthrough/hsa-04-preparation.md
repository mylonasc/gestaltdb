# HSA-04 first slice: one-use locator preparation containers

Parent #451; work package #455. Partial delivery, not completed native batching.

## Changes and retained contracts

- Consume each one-use expected-membership map directly, rather than copying it.
- Consume authenticated native rows through a callback, rather than retaining a
  full intermediate result list before reducing it to candidate IDs or a winner.
- Store version references in logical histories without redundant locator tuples.
- Complete catalog authentication, broad world scan, all dimension scans,
  point-materialization checks, independent cumulative bounds, snapshot lifetime,
  full grouped canonical conformance and missing-retraction checks are unchanged.
- Existing output cannot escape until all native membership checks and canonical
  conformance succeed. No public/on-disk change or automatic native adoption.

Five additional tests cover tiny native chunks and late omitted/duplicated tails
after earlier rows have already been consumed, with failure before first evidence,
resource cleanup and no completed profile on failure.

## Allocation diagnostic

`benchmarks/reasoning_locator_allocations.py` runs the existing write-receipt
parity harness with 1K dense histories, plain PyRex, JSON, all claims at time 50,
and three fresh-handle reads in one process. It traces Python allocations only
during `_read_winners`; native buffers, earlier view construction and total RSS
are explicitly outside that measurement.

Run the same script by absolute path from both checkouts:

```bash
uv run --with pyarrow --with pyrex-rocksdb python \
  /absolute/path/to/benchmarks/reasoning_locator_allocations.py > /tmp/opencode/alloc.json
```

Before: `8c5413d`; after: `45031fe`. Reports record adapter and script hashes:

- `benchmarks/results/reasoning-455-hsa-04-alloc-before-1k.json`
- `benchmarks/results/reasoning-455-hsa-04-alloc-after-1k.json`

Median traced peak: 3,557,630 → 3,441,666 bytes (~3.3% reduction). This modest
whole-preparation diagnostic is not an RSS ceiling or a general memory-speedup
claim. All six exact fact/version parity observations pass; scan counts stay
1001 and physical row profiles are unchanged. No scan/read improvement is claimed.

Remaining HSA-04 work: investigate actual native range-union/point-fetch APIs,
chunk/overlap/order/omission contracts and cumulative physical-row/byte accounting
before admitting batching; expand selective/nonselective and larger-scale
allocation/latency measurements. Do not remove world completeness or canonical
checks based on candidate hits. HSA-05 remains optional and measurement-gated;
HSA-06's final-source cross-consumer acceptance is still mandatory.
