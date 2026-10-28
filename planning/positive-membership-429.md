# Memory-mappable positive membership (#429)

Parent: #106. Branch: `feature/memmap-positive-membership`; target: `main`.
This is separate from sparse relation directories (#427 / PR #428); neither
implementation logically depends on the other. Shared snapshot hooks/docs may
need integration when the first PR merges. No merge/release is part of this slice.

## Contract

- New non-temporal snapshots persist exact lexicographically sorted unique int64
  `(source, relation, target)` keys in `positive_membership_triples.npy`.
- The authenticated v2 envelope marks
  `positive_membership_encoding="lexicographic-triples-v1"`. Added artifacts,
  schemas and exact canonical membership are independently checked on load;
  unknown encodings and incomplete/malformed indexes fail closed.
- Temporal snapshots reuse `positive_history_triples`: sorted key row numbers
  directly identify the existing validity groups, with no duplicate key file.
- Engines borrow persisted keys directly, including read-only memmaps; no
  edge-sized Python positive-code set or history-group dictionary is built.
  Static legacy snapshots build sorted keys once in RAM. Pre-history temporal
  compatibility admission still derives history arrays as before.
- Binary search compares all three coordinates without uint64 packing, copies
  of entire strided columns, RNG draws or truncation. Existing interval and
  negative-rejection policies remain unchanged. `positive_triples` stays aligned
  with edge rows, including duplicate triples.
- The new optional snapshot field is appended after existing dataclass fields,
  preserving earlier positional constructor slots. Supported non-temporal v2
  append, prune/compact and weight rewrites refresh membership artifacts.

## Memory and compatibility boundaries

For U unique triples, static persisted keys require 24 * U payload bytes. This
replaces Python object-heavy set membership, not all O(E) arrays. Validation and
builds still allocate; legacy static engine admission sorts once; weight checks,
candidate/result buffers and OS page cache remain. No constant-RSS or zero-I/O
claim. Borrowed arrays/files must not be mutated/replaced under live readers.

Lookup changes from expected O(1) hashing to O(log U) exact binary search.
The measured lookup slowdown is recorded, not hidden behind a memory claim.
Old loaders reject new static artifact catalogs; temporal files add only the
membership marker and use existing history arrays, so older history-aware v2
loaders remain compatible. No on-load disk rewrite or v1 integrity claim.

## Validation

- New static membership tests: **28 passed** on Python 3.12 / NumPy 2.5.2 and
  Python 3.10 / NumPy 1.23.5. Tests cover borrowed RAM/memmap keys, forbidden
  startup rebuilds, v1/v2 fallback, sorted/duplicate/missing/extra/malformed keys,
  checksums, unknown markers, large coordinates, hash-oracle negative parity,
  deterministic exhaustion, and append/prune/weight rewrite freshness.
- Membership plus temporal read-view suite: **81 passed**, including temporal
  history reuse, retraction gaps, boundaries, point/window policies and seeded
  negative diagnostics compared with an independent hash-group oracle.
- Final full Python 3.12 suite with LMDB, LevelDB and MessagePack at source
  `40024de`: **3187 passed, 652 optional-dependency skips**.
- Ruff, strict mypy ratchet for the new module, module-size gate, lockfile
  consistency, docs drift and Sphinx warnings-as-errors passed. Modular examples:
  **5 passed / 3 optional skips**. Wheel/sdist builds and twine checks passed;
  installed-wheel smoke outside the checkout verified deduplication, mapped key
  identity and exact positive/negative membership.

## Measurement

`benchmarks/results/positive-membership-429.json` is a small reproducible
membership-component comparison at source `40024de` (10,000 edges / 9,999 unique
triples). Python set retained-object size is 804,476 bytes versus 239,976 bytes
of mapped triple payload. These are different categories, not an RSS reduction
measurement. Under tracemalloc, set construction peaks at 1,843,696 traced bytes;
constructing the borrowing mapped-index wrapper peaks at 312. Sorting/building
the persisted keys is separately measured and is not free.

For 1,000 mixed lookups, the set takes about 0.68ms and the mapped binary search
about 7.41ms, with identical answers. Startup timings are instrumented with
tracemalloc; neither these figures nor payload bytes establish full-engine
startup/negative-sampling throughput, cold-cache or multi-worker performance.
