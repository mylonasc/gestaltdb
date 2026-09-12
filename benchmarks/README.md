# GestaltDB Benchmarking Suite

This directory contains the modular benchmarking and profiling suite for GestaltDB. It includes microbenchmarks, multi-dimensional matrix runners, LSM compaction stress tests, GNN sampler comparisons, embedded and external database shootouts, parameter tuning sweeps, and profile breakdown tools.

---

## Directory Layout

```
benchmarks/
├── README.md               # This documentation
├── __init__.py             # Package declaration
├── __main__.py             # Module entry point (`python -m benchmarks`)
├── cli.py                  # Unified CLI dispatcher
│
├── common/                 # Reusable building blocks and shared logic
│   ├── __init__.py         # Re-exports common helpers
│   ├── timing.py           # Wall-clock timers, reservoir sampling, CPU affinity
│   ├── datasets.py         # Deterministic graph generators (synthetic, star, typed path)
│   ├── storage.py          # Backend opening, serializer factories, dependency checks
│   ├── reporting.py        # Base row builders, rate metrics, CSV/JSONL result writers
│   └── workloads.py        # GestaltDB traversal and query implementations
│
├── quick.py                # Single-run ingestion & sampling microbenchmark
├── matrix.py               # Matrix runner across backends, sizes, cores, and ingest modes
├── compaction.py           # LSM overwrite & compaction pressure benchmark
├── sampler.py              # SamplerEngine array sampler vs baseline graph traversal
├── embedded.py             # Embedded graph databases: GestaltDB vs LatticeDB vs LadybugDB
├── external.py             # External graph databases: GestaltDB vs Neo4j, Memgraph, ArcadeDB, AGE
├── arcadedb.py             # Focused comparison between GestaltDB and ArcadeDB embedded
├── tuning.py               # RocksDB tuning parameter sweep
├── profiling.py            # Ingestion phase bottleneck breakdown with cProfile
└── plotting.py             # SVG visualization generator for benchmark summaries
```

---

## Installation & Requirements

Install GestaltDB with the dependencies required for your benchmarking target:

```bash
# Core backends and fast columnar ingestion (RocksDB, LevelDB, Arrow, Polars)
uv sync --extra fast-ingest --extra rocksdb --extra leveldb

# External graph database engines (Neo4j, Memgraph, ArcadeDB, Apache AGE)
uv sync --extra external-bench

# Everything needed for development and benchmarks
uv sync --extra all
```

---

## Running Benchmarks

You can run benchmarks in two ways:

### 1. Unified CLI (`python -m benchmarks`)

The suite provides a single CLI entry point with command discovery:

```bash
# View all available benchmark subcommands
python -m benchmarks --help

# Run a specific benchmark
python -m benchmarks quick --backend leveldb --nodes 10000 --edges 50000
python -m benchmarks matrix --backends rocksdb --sizes 10000 100000 --cores 2 4
python -m benchmarks compaction --configs leveldb rocksdb-p4-bg4-largebuf --keys 100000
python -m benchmarks sampler --nodes 1000 --edges 500000 --iterations 100
python -m benchmarks embedded --engines gestaltdb latticedb --nodes 10000 --edges 50000
python -m benchmarks external --engines gestaltdb arcadedb --workloads neighbors star_traversal
python -m benchmarks tuning --nodes 20000 --edges 100000
python -m benchmarks profiling --cases rocksdb-json-polars --nodes 10000 --edges 50000
python -m benchmarks plotting benchmark_results/external_graphdbs/external_graphdbs_summary.jsonl
```

### 2. Standalone Scripts

Each benchmark script is also directly runnable:

```bash
uv run python benchmarks/quick.py --backend rocksdb --nodes 20000 --edges 100000 --append-only
uv run python benchmarks/matrix.py --backends leveldb rocksdb --sizes 10000 50000
uv run python benchmarks/compaction.py --configs rocksdb-p4-bg4-smallbuf --keys 50000
uv run python benchmarks/sampler.py --nodes 1000 --edges 100000
```

### Backward Compatibility

Legacy script invocations from repository root and `scripts/` are preserved as forwarders:
- `python benchmarks.py` forwards to `benchmarks/quick.py`.
- `python scripts/benchmark_matrix.py` forwards to `benchmarks/matrix.py`.
- `python scripts/benchmark_rocksdb_compaction.py` forwards to `benchmarks/compaction.py`.
- `python scripts/benchmark_sampler_engine.py` forwards to `benchmarks/sampler.py`.
- `python scripts/benchmark_embedded_graphdbs.py` forwards to `benchmarks/embedded.py`.
- `python scripts/benchmark_external_graphdbs.py` forwards to `benchmarks/external.py`.
- `python scripts/benchmark_arcadedb_vs_gestaltdb.py` forwards to `benchmarks/arcadedb.py`.
- `python scripts/tune_rocksdb.py` forwards to `benchmarks/tuning.py`.
- `python scripts/profile_ingestion.py` forwards to `benchmarks/profiling.py`.
- `python scripts/plot_external_graphdbs.py` forwards to `benchmarks/plotting.py`.

---

## Implemented Benchmarks

### 1. Quick Microbenchmark (`benchmarks/quick.py`)
- **Purpose**: Fast smoke benchmark to catch performance regressions in node/edge writes and typed traversal sampling on a single backend.
- **Backends**: `leveldb`, `rocksdb`, `lmdb`.
- **Key Flags**:
  - `--backend {leveldb,rocksdb,lmdb}`: Storage engine.
  - `--nodes <int>` (default `10000`): Number of nodes.
  - `--edges <int>` (default `50000`): Number of edges.
  - `--append-only`: Skip existing edge reads during bulk insert.
  - RocksDB tuning: `--rocksdb-parallelism`, `--rocksdb-bloom-bits`, `--rocksdb-transactional`, etc.
- **Example**:
  ```bash
  python -m benchmarks quick --backend rocksdb --nodes 20000 --edges 100000 --append-only --rocksdb-bloom-bits 10
  ```

### 2. Multi-Dimensional Matrix Benchmark (`benchmarks/matrix.py`)
- **Purpose**: Exhaustive parameter sweep across backend types, graph sizes, worker core counts, serializers, and ingestion modes. Databases are written, closed, and reopened from disk before query execution to ensure cold page cache behavior.
- **Dimensions**:
  - Backends: `leveldb`, `rocksdb`.
  - Ingestion modes: `object` (pure Python objects), `arrow` (PyArrow columnar), `polars` (Polars columnar).
  - Serializers: `pickle`, `msgpack`, `json`, `protobuf`.
  - RocksDB presets: `default`, `transactional`, `parallel`, `parallel-buffer64mb-bloom10`, `parallel-buffer64mb-bloom10-nowal`, etc.
- **Outputs**: `matrix_results.csv` and `matrix_results.jsonl`.
- **Example**:
  ```bash
  python -m benchmarks matrix \
      --backends leveldb rocksdb \
      --sizes 10000 100000 \
      --cores 1 2 4 \
      --ingestion-modes object arrow polars \
      --output-dir benchmark_results/matrix_run
  ```

### 3. LSM Compaction Pressure Benchmark (`benchmarks/compaction.py`)
- **Purpose**: Evaluates write amplification, write stalls, and background compaction throughput under repeated overwrites. Tracks real-time SST and write-ahead log file counts.
- **Key Flags**:
  - `--configs`: One or more presets (`leveldb`, `rocksdb-p1-bg1-smallbuf`, `rocksdb-p4-bg4-smallbuf`, `rocksdb-p8-bg8-smallbuf`, `rocksdb-p4-bg4-largebuf`).
  - `--keys <int>` (default `250000`): Unique keys per pass.
  - `--passes <int>` (default `6`): Total overwrite passes.
  - `--key-order {permuted,sequential}`: Permuted generates overlapping SST ranges that trigger background compaction.
- **Outputs**: `compaction_pressure_results.csv` and `compaction_pressure_results.jsonl`.
- **Example**:
  ```bash
  python -m benchmarks compaction --configs leveldb rocksdb-p4-bg4-largebuf --keys 100000 --passes 4
  ```

### 4. Sampler Engine Benchmark (`benchmarks/sampler.py`)
- **Purpose**: Compares GestaltDB's ML training sampler engine (`SamplerEngine` with pre-built numpy array snapshots) against the baseline dictionary-based neighbor sampling protocol.
- **Workload**: Batch neighbor sampling across multiple relation types with configurable fanout.
- **Example**:
  ```bash
  python -m benchmarks sampler --nodes 10000 --edges 500000 --batch-nodes 512 --fanout 15 --iterations 100
  ```

### 5. Embedded Graph Databases Benchmark (`benchmarks/embedded.py`)
- **Purpose**: Compares GestaltDB (RocksDB) against local embedded Python graph databases:
  - **GestaltDB**: Attributed property graph on RocksDB with native typed adjacency.
  - **LatticeDB**: Embedded graph database using native transactions.
  - **LadybugDB**: Embedded graph engine using CSV COPY ingestion and Cypher queries.
- **Workloads**: `ingest`, `neighbors`, `sample_neighbors`, `star_traversal`, `bfs_depth`, `typed_path`.
- **Outputs**: Raw run logs (`embedded_graphdbs_raw.csv`) and aggregated mean ± std summaries (`embedded_graphdbs_summary.csv`).
- **Example**:
  ```bash
  python -m benchmarks embedded --engines gestaltdb latticedb --nodes 50000 --edges 250000 --repetitions 3
  ```

### 6. External Graph Databases Benchmark (`benchmarks/external.py`)
- **Purpose**: Compares GestaltDB (regular and transactional) with server-based graph engines running in disposable Docker containers:
  - **GestaltDB / GestaltDB tx**: Columnar Arrow entity ingestion and Cypher/traversal queries.
  - **Neo4j**: Bolt connection, batched Cypher UNWIND ingestion and queries.
  - **Memgraph**: In-memory property graph with Bolt and Cypher.
  - **ArcadeDB**: Embedded Java-based graph database.
  - **Apache AGE**: PostgreSQL extension for graph queries through `cypher()`; optional GIN indexing is enabled with `--age-require-index`.
- **Outputs**: `external_graphdbs_raw.csv` and `external_graphdbs_summary.csv`.
- **Example**:
  ```bash
  python -m benchmarks external \
      --engines gestaltdb gestaltdb-tx arcadedb \
      --workloads columnar_ingest neighbors star_traversal bfs_depth typed_path \
      --nodes 100000 --edges 500000 --repetitions 3
  ```

### 7. ArcadeDB Focused Benchmark (`benchmarks/arcadedb.py`)
- **Purpose**: Direct head-to-head evaluation between GestaltDB's RocksDB backend and ArcadeDB embedded using ArcadeDB's `GraphBatch` API.
- **Workloads**: `columnar_ingest`, `star_traversal`, `bfs_depth`, `typed_path`.
- **Example**:
  ```bash
  python -m benchmarks arcadedb --engines gestaltdb arcadedb --nodes 50000 --edges 250000
  ```

### 8. RocksDB Tuning Parameter Sweep (`benchmarks/tuning.py`)
- **Purpose**: Automates benchmarking across 15+ RocksDB configurations (varying parallelism, buffer sizes from 64MB to 256MB, Bloom filter bits, and WAL modes) against a LevelDB baseline.
- **Outputs**: JSON and CSV files reporting write rates and sampling throughput per configuration.
- **Example**:
  ```bash
  python -m benchmarks tuning --nodes 20000 --edges 100000 --output-prefix benchmark_results/rocksdb_tuning_sweep
  ```

### 9. Ingestion Profiler (`benchmarks/profiling.py`)
- **Purpose**: Breaks down end-to-end ingestion time using `cProfile` and `pstats` across four isolated phases:
  1. Payload serialization (Python objects vs Polars `struct.json_encode`).
  2. Node ingestion and ID key generation.
  3. Edge ingestion and typed adjacency indexing.
  4. Secondary index creation (inline vs deferred rebuilding).
- **Outputs**: `.prof` profiling binaries, sorted text summaries, and `summary.json`.
- **Example**:
  ```bash
  python -m benchmarks profiling --cases rocksdb-json-polars rocksdb-json-python --nodes 50000 --edges 250000
  ```

### 10. Summary Plotting Utility (`benchmarks/plotting.py`)
- **Purpose**: Generates publication-ready SVG charts from external benchmark summary JSONL files:
  - Ingestion throughput bar chart (`external_graphdb_ingest_100k.svg`).
  - Query latency heatmap (`external_graphdb_queries_100k.svg`).
- **Example**:
  ```bash
  python -m benchmarks plotting benchmark_results/external_graphdbs/external_graphdbs_summary.jsonl --output-dir docs/_static
  ```

---

## Validation Notes

The benchmark package was validated with `uv sync --extra all` on small smoke-test graph sizes. The following command paths completed locally:

- `quick` across LevelDB, RocksDB, and LMDB.
- `matrix` across LevelDB/RocksDB and object, Arrow, and Polars ingestion modes.
- `compaction`, `sampler`, `tuning`, `profiling`, and `plotting`.
- `embedded` across GestaltDB, LatticeDB, and LadybugDB using the current `ladybug.Connection(db)` API.
- `arcadedb` and external ArcadeDB using the current `arcadedb_embedded.create_database` API.
- `external` across GestaltDB, transactional GestaltDB, ArcadeDB, Neo4j, and Memgraph for all advertised workloads.

Apache AGE rows are recorded as benchmark failures when the configured Docker image is unavailable. Inspect `status` and `skip_reason` in raw CSV/JSONL outputs before interpreting timings.

---

## Workload Reference

| Workload | Target Pattern | Description |
|:---|:---|:---|
| `ingest` | Batch write | Bulk node creation followed by batched edge insertions. |
| `columnar_ingest` | Columnar ingest | Zero-copy / Arrow binary column ingestion bypassing Python row objects. |
| `neighbors` | 1-hop scan | Expands outgoing edges for a list of seed nodes. |
| `sample_neighbors` | 1-hop sampling | Samples outgoing edges with reservoir sampling up to `sample_size`. |
| `star_traversal` | Hub expansion | Repeatedly traverses outgoing edges from a single high-degree hub (`n0`). |
| `bfs_depth` | Breadth-first search | Explores neighbors level by level up to `depth` or `bfs_limit`. |
| `typed_path` | 2-hop traversal | Follows `(seed)-[:RelA]->(b)-[:RelB]->(c)` path patterns. |
| `deep_typed_query` | 3-hop traversal | Follows `(seed)-[:RelA]->(b)-[:RelB]->(c)-[:RelC]->(d)` path patterns. |

---

## Shared Common Utilities (`benchmarks/common/`)

When writing new benchmarks, import shared utilities from `benchmarks.common`:

```python
from benchmarks.common import (
    # Timing & Execution
    seconds, timed, cpu_affinity, set_thread_env, reservoir_count,
    # Datasets & Graph Shapes
    chunks, edge_parts, make_node, make_edge, seed_ids, workload_graph_shape,
    # Storage & Backends
    open_gestaltdb, serializer_factory, rocksdb_configs, disk_usage, file_stats,
    # Reporting
    base_row, add_rates, count_status, summarize_rows, ResultWriter,
    # GestaltDB Workloads
    gestaltdb_bfs, gestaltdb_neighbors, gestaltdb_sample_neighbors,
    gestaltdb_star_traversal, gestaltdb_typed_path, gestaltdb_deep_typed_query,
)
```
