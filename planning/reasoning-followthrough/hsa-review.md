# HSA review and authentication follow-up — #451 / #453

## Review disposition

- #459 and #463 were already merged into their stacked bases.
- #450 (benchmark-only, all published checks green) was marked ready and merged
  into main; #461 was retargeted to main and merged after focused regression tests.
- Removed the unsupported approximate 1K comparison from #461's description.
  Its earlier 10K after numbers remain explicitly preliminary local observations.
- Follow-up fixes incoming-entry byte accounting, oversized-entry admission,
  incorrect per-store tuple destructuring, decoder binding, and cache retirement
  on store replacement, graph close, transaction/snapshot exit and failed or
  interrupted commit/batch reads. Byte accounting is serialized sizes plus
  descriptors/digests, **not decoded heap or RSS**.
- Replaced superficial rebinding coverage with the real store setter; added a
  real verifiable LMDB snapshot held across a correction, transaction interruption,
  close failure, warm-target tampering, decoder replacement and byte pressure.
- Extracted store/scope lifecycle into `_graph/scopes.py`; lowered the GraphDB
  size budget from 1909 to 1887 and refreshed the generated API index.
- Fixed HSA-02's `dict[str, object]` typing regression without weakening the gate.

## Measurements, not an adoption claim

Committed reports use the existing isolated worker harness, plain PyRex, JSON,
1000 records per commit, dense history, valid time 50 and `all` selection. 1K
reports repeat three times for each first/warm configuration. All 36 before/after
1K extraction observations pass exact fact/version-ID parity.

| Source, first | Before metadata reads | After metadata reads | Before p50 s | After p50 s |
| --- | ---: | ---: | ---: | ---: |
| Ordinary | 857184 | 24152 | 1.549 | 0.140 |
| Full-record | 1867881 | 40728 | 3.417 | 0.246 |
| Locator | 1545706 | 37176 | 2.875 | 0.329 |

Warm ordinary/full-record decode counts fall from 1001/2116 to zero. Locator
warm decodes fall from 3116 to 1343: its independent point-materialization checks
still decode physical records. Mutable envelope authentication remains mandatory.

An intermediate diagnostic without request-local commit grouping regressed
latency despite reducing decodes. Retaining compact authenticated digests avoids
reparsing descriptor JSON, but the decisive read reduction is authenticating each
shared target commit **once per resolver invocation**, not once per target ID.
The request-local map never survives the call; every subsequent mutable request
rechecks descriptor/marker/envelopes and supersession targets. No filtered result
cache, persistent authentication shortcut, changed digest or native default.

These are local observations, not a controlled latency distribution: OS caches
are uncontrolled, instrumentation overhead is included, and the before run
overlapped part of a test run. The deterministic read/decode counters are stronger
evidence than elapsed-time ratios. RSS is reported per whole worker; no memory
speedup or RSS ceiling is inferred. HSA-03's broad allocation/lifetime acceptance
and HSA-04's scan/copy/row-budget work remain open.

### Reproduction

Before revision `43d0fb4`; after revision `f549930`. Run in each checkout:

```bash
uv run --with pyarrow --with pyrex-rocksdb python -m benchmarks.reasoning_locator_pipeline \
  --rows 1000 --commit-batch 1000 --instrument --repeats 3 \
  --methods ordinary native-full-record native-authenticated-locator \
  --selections all --times 50 --caches first warm --output /tmp/opencode/hsa-review.json
```

The additional after-only 10K report uses `--rows 10000 --repeats 1 --caches first`;
it is not a matched repeated before/after scale curve.

### Report SHA-256

All paths below are relative to `benchmarks/results/`.

- `reasoning-453-hsa-03-review-before-1k.json`:
  `6e78147a4839301a5019a4e857fe4f022a2efb36cd5090c701c64b11a6dff27c`
- `reasoning-453-hsa-03-review-after-1k.json`:
  `a20d88240ed0334b4f47cd219b2cde045c494af2a5aed0643998a58c4dfb4b03`
- `reasoning-453-hsa-03-review-after-10k.json`:
  `0013c1695e67601f0c7df356d0621a9265257829ec8a934a8ab16eaa4e4e32b0`
