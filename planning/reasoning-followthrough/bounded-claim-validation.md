# Bounded authenticated claim-read work — #314

Target: `feature/onto-dev-epic-2`, never `main`. Parent review #200 / roadmap #156;
unblocks the larger authenticated measurement portion of #194. PR #315.

## Protocol and unchanged boundaries

`iter_claims_as_of` groups candidate locators by commit, validates every touched
commit through the existing descriptor/marker/full-envelope path, and resolves
candidate identities against complete logical histories at one captured horizon.
This avoids recomputing the latest system horizon and rereading whole commits
for every logical fact. Selected winners are immutable `ClaimVersion` values.

An operation-local marker catalog detects historical rewrites/removals. All
touched commits are fully revalidated on successful iterator exhaustion,
including unrelated records in those commits. Append-only writes beyond the
captured horizon do not change the selected answers. Consumers must fully
consume the iterator and discard partial output on error; early close is not
authenticated completion. There is no new backend snapshot, live-cache trust
shortcut, or persisted format. Individual node/edge/claim version reads,
supersession policy, detached mutable entity payloads and backend capability
contracts are unchanged. No second storage handle is opened.

Validation I/O scales with selected locators and touched commit sizes rather
than their product. The selected locator/winner buffers consume Python memory;
neither a hard RSS limit nor constant-memory execution is promised. This does
not make unsupported snapshot requests work on LevelDB or plain PyRex.

## Deterministic regression evidence

The loaded-handle 48-row / 8-per-commit regression failed before the change:
**1160 envelope reads**, exceeding its 384-read allowance. After grouping:

| Backend | Commit batch | Loaded handle reads | Reopened handle reads |
| --- | ---: | ---: | ---: |
| LMDB | 8 | 160 | 160 |
| LevelDB | 8 | 160 | 160 |
| PyRex | 8 | 160 | 160 |
| LMDB | 48 | 240 | 240 |
| LevelDB | 48 | 240 | 240 |
| PyRex | 48 | 240 | 240 |

`tests/test_temporal_claim_read_work.py` enforces I/O-work bounds, not wall-clock
thresholds. It also tests unrelated-row corruption, damaged marker/descriptor,
corruption/removal/valid descriptor rewrite after the first yield, appended
facts beyond the captured horizon, historical point/filter parity including
correction/retraction/resurrection, store rebinding and pinned LMDB isolation.
The earlier #282 cache integrity and mutable-payload isolation tests still run.

## Repeated measurement evidence

Clean source **`49231f3bddc5b916dace9fa73ef2092089699137`**, based on integration
`f6342d5`. Python 3.13.11, LMDB 2.2.1, PyRex 0.5.0, Arrow 24.0.0. Benchmark source
hashes and recorded runtime package versions match the earlier #194 matrix.
All reports have `complete=true`, a clean-source flag and three samples per case:

- `benchmarks/results/reasoning-314-ladder.json`: 12 cases / 36 samples,
  1K/10K/100K assertions, LMDB/PyRex, first/primed extraction, fixed padding.
- `benchmarks/results/reasoning-314-coverage-10k.json`: 24 cases / 72 samples,
  both backends, all/predicate/subject selections, fixed/variable padding and
  first/primed extraction. This is the coverage expansion previously gated off.
- `benchmarks/results/reasoning-314-variable-100k.json`: 4 cases / 12 samples,
  both backends and cache labels at 100K with variable 32–4096-byte padding.

Every one of the **120 samples** verifies exact fact/version evidence against
the reference iterator and classification closure against an analytical result.

### First-read fixed-payload extraction (seconds)

| Assertions | Backend | Prior #194 median | Grouped median | Grouped nearest-rank P95 |
| --- | --- | ---: | ---: | ---: |
| 1K | LMDB | 2.342 | 0.025 | 0.025 |
| 1K | PyRex | 4.179 | 0.039 | 0.040 |
| 10K | LMDB | 48.042 | 0.223 | 0.225 |
| 10K | PyRex | 84.215 | 0.340 | 0.342 |
| 100K | LMDB | timeout, no timing | 2.403 | 2.405 |
| 100K | PyRex | timeout, no timing | 4.207 | 4.220 |

The prior 100K timeouts were **whole-worker 300-second limits**, not 300-second
extraction measurements; do not compute speedup ratios from those failures.
The original incomplete reports remain committed unchanged.

At 100K fixed payload, first-read reasoning-pipeline medians are **4.355 s LMDB /
6.713 s PyRex**, with evaluation about 1.66/1.67 s. First-read variable-payload
extraction medians are **2.560 / 4.806 s**, with pipelines **4.577 / 7.585 s**.
Construction is separate (roughly 19–26 s for fixed and 22 s for variable in the
recorded cases, with sample variance). Across first/primed runs, whole-worker
peak RSS, including load, reference validation and oracle closure, reached about
1471/630 MiB fixed and 2244/1060 MiB variable for LMDB/PyRex. These are not
engine-only memory numbers.

Cache state means first/primed extraction on a reopened handle, not OS-cold.
Padding is compressible repeated `x` in claim provenance, not oversized indexed
entity IDs. Three samples support repeatability, not robust tail-latency or a
universal backend ranking. These are ordinary authenticated Python claim reads;
they do **not** establish native Arrow acceleration, and do not measure support
discovery, managed publication or recursive frontier execution.

## Reproduction

```sh
python -m benchmarks.reasoning_matrix --sizes 1000 10000 100000 \
  --backends lmdb pyrex --caches first primed --repeats 3 \
  --output /tmp/opencode/onto-314-ladder.json
python -m benchmarks.reasoning_matrix --sizes 10000 --backends lmdb pyrex \
  --selections all predicate subject --payloads fixed variable \
  --caches first primed --repeats 3 --output /tmp/opencode/onto-314-coverage-10k.json
python -m benchmarks.reasoning_matrix --sizes 100000 --backends lmdb pyrex \
  --payloads variable --caches first primed --repeats 3 \
  --output /tmp/opencode/onto-314-variable-100k.json
```

This closes #314's grouped-read and measured rerun scope, not all of #194 or the
broader phase durability/integration gates. Real native-claims decoder/locator
integration, entity-ID rejection studies, recursive demand/frontier workloads,
and support/publication accounting remain explicitly tracked rather than
silently declared delivered.
