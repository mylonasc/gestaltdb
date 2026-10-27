# Sparse sampler relation directories (#427)

Parent: #106. Branch: `feature/sparse-relation-adjacency`; target: `main`.

## Contract and scope

New builds and supported non-temporal v2 rewrites persist sparse populated
endpoint/relation directories. The authenticated v2 envelope carries the exact
`relation_adjacency_encoding="sparse-rows-v1"` discriminator and authenticates
every `row_keys`, `indptr`, and edge-index array. Dense legacy v1/v2 loading
remains unchanged; unknown encodings fail closed. Older loaders reject the new
artifact catalog. This is an explicit encoding extension, not temporal format
v3, an on-load migration, or partitioned sampling.

`CSRAdjacency.edge_range(key)` is representation-independent. Sparse `indptr`
addresses populated rows rather than all possible node/relation pairs. Stable
edge order and temporal valid-start/tie order remain unchanged. Node-only CSR,
engine candidate buffers and Python positive-membership sets are not changed.
Directory storage is O(populated pairs), but binary search replaces O(1) dense
offset lookup. Validation uses row/edge-sized working arrays, not dense N*R
reconstruction; no constant-memory or out-of-core build guarantee is claimed.

## Validation

- Initial full Python 3.12 suite with LMDB, LevelDB and MessagePack:
  **3177 passed, 652 optional-dependency skips**.
- New sparse tests: **25 passed** on both Python 3.12 / NumPy 2.5.2 and
  Python 3.10 / NumPy 1.23.5, including dense v1/v2 compatibility, RAM/memmap,
  seeded neighbor/walk/multihop/negative batches, empty/absent rows, malformed
  resealed artifacts, checksums and supported legacy rewrites.
- Broader focused sampling/readview/consistency/inferred-admission regression:
  **432 passed, 49 skipped** before the final additional rewrite/parity cases.
- Ruff, strict adjacency mypy contracts, module-size ratchet, documentation
  drift and Sphinx warnings-as-errors passed. Modular examples: **5 passed,
  3 optional-dependency skips**.

## Measurement

`benchmarks/results/sparse-adjacency-427.json` records a reproducible small
heterogeneous directory microbenchmark at source revision `74564ad`.
For one direction with 2,000 nodes, 128 relations and 5,000 edges, directory
payload is **2,048,008 dense bytes versus 79,256 sparse bytes** (about 96% less).
Candidate sequences match exactly. The 5,000 mixed present/absent lookups take
about **1.48ms dense versus 5.48ms sparse**; lookup plus fanout-two draws takes
about **10.56ms versus 14.35ms**. This is a measured memory/latency tradeoff,
not a universal speedup, peak-RSS or full-engine/multi-worker result.

Remaining #106 work includes memory-mappable positive membership; partitioned
snapshots stay separately scoped in #121. No version bump or release is included.
