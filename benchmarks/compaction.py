"""Target LSM compaction pressure for LevelDB and RocksDB backends.

PyRex currently exposes RocksDB options and basic read/write methods, but not
RocksDB properties such as compaction-pending, level sizes, or statistics. This
benchmark therefore uses a write-amplifying overwrite workload and records per
pass throughput plus on-disk SST/log file evolution as indirect evidence of LSM
flush/compaction behavior. The default permuted key order creates overlapping
SST ranges, which is the case where RocksDB background compaction parallelism is
expected to matter.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from gestaltdb.kvstores import LevelDBStore, PyRexStore
from benchmarks.common.reporting import ResultWriter
from benchmarks.common.storage import file_stats

CSV_FIELDS = [
    "config",
    "backend",
    "pass_index",
    "keys",
    "batch_size",
    "value_size",
    "write_buffer_size",
    "parallelism",
    "max_background_jobs",
    "disable_wal",
    "key_order",
    "seconds",
    "writes_per_second",
    "mb_per_second",
    "sst_files",
    "sst_bytes",
    "log_files",
    "log_bytes",
    "total_files",
    "total_bytes",
    "close_seconds",
]


def make_value(pass_index: int, value_size: int) -> bytes:
    """Construct a deterministic payload identifiable by pass index."""
    prefix = f"pass={pass_index:04d}|".encode("ascii")
    if len(prefix) >= value_size:
        return prefix[:value_size]
    return prefix + (b"x" * (value_size - len(prefix)))


def key_for_position(position: int, keys: int, pass_index: int, key_order: str) -> int:
    """Determine key index given write position, key count, pass, and ordering scheme."""
    if key_order == "sequential":
        return position
    if key_order == "permuted":
        return ((position * 1_000_003) + (pass_index * 9_176)) % keys
    raise ValueError(f"unknown key order: {key_order}")


def write_pass(
    store: Any,
    backend: str,
    keys: int,
    batch_size: int,
    pass_index: int,
    value_size: int,
    key_order: str,
) -> float:
    """Execute one full write/overwrite pass over the key range."""
    value = make_value(pass_index, value_size)
    start = time.perf_counter()
    for offset in range(0, keys, batch_size):
        end = min(offset + batch_size, keys)
        if backend == "rocksdb":
            batch = store._pyrex.PyWriteBatch()
            for position in range(offset, end):
                index = key_for_position(position, keys, pass_index, key_order)
                key = store._key(b"C", f"k{index:012d}".encode("ascii"))
                batch.put(key, value)
            store.db.write(batch, store.write_options)
        else:
            batch_values = {}
            for position in range(offset, end):
                index = key_for_position(position, keys, pass_index, key_order)
                batch_values[f"k{index:012d}".encode("ascii")] = value
            store.put_edges_bulk(batch_values)
    return time.perf_counter() - start


def config_options(config: str, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Resolve storage configuration options for the specified preset."""
    if config == "leveldb":
        return "leveldb", {}
    if config == "rocksdb-p1-bg1-smallbuf":
        return "rocksdb", {"parallelism": 1, "max_background_jobs": 1, "write_buffer_size": args.write_buffer_size}
    if config == "rocksdb-p4-bg4-smallbuf":
        return "rocksdb", {"parallelism": 4, "max_background_jobs": 4, "write_buffer_size": args.write_buffer_size}
    if config == "rocksdb-p8-bg8-smallbuf":
        return "rocksdb", {"parallelism": 8, "max_background_jobs": 8, "write_buffer_size": args.write_buffer_size}
    if config == "rocksdb-p4-bg4-largebuf":
        return "rocksdb", {"parallelism": 4, "max_background_jobs": 4, "write_buffer_size": 64 * 1024 * 1024}
    raise ValueError(f"unknown config: {config}")


def run_config(config: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Execute all passes for a given backend configuration."""
    backend, options = config_options(config, args)
    if backend == "rocksdb" and args.disable_wal:
        options["disable_wal"] = True
    path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_compaction_{config}_", dir=args.tmp_dir))
    rows: list[dict[str, Any]] = []
    store = None
    try:
        store = PyRexStore(path=str(path), **options) if backend == "rocksdb" else LevelDBStore(path=str(path))
        for pass_index in range(args.passes):
            elapsed = write_pass(store, backend, args.keys, args.batch_size, pass_index, args.value_size, args.key_order)
            stats = file_stats(path)
            row: dict[str, Any] = {
                "config": config,
                "backend": backend,
                "pass_index": pass_index,
                "keys": args.keys,
                "batch_size": args.batch_size,
                "value_size": args.value_size,
                "write_buffer_size": options.get("write_buffer_size"),
                "parallelism": options.get("parallelism"),
                "max_background_jobs": options.get("max_background_jobs"),
                "disable_wal": bool(options.get("disable_wal", False)),
                "key_order": args.key_order,
                "seconds": elapsed,
                "writes_per_second": args.keys / elapsed if elapsed > 0 else 0,
                "mb_per_second": ((args.keys * args.value_size / 1_000_000) / elapsed) if elapsed > 0 else 0,
                **stats,
                "close_seconds": "",
            }
            rows.append(row)
            print(
                f"{config} pass={pass_index} {row['writes_per_second']:.0f} writes/s "
                f"sst_files={row['sst_files']} sst_mb={row['sst_bytes'] / 1_000_000:.1f}",
                flush=True,
            )
        start = time.perf_counter()
        store.close()
        store = None
        close_seconds = time.perf_counter() - start
        if rows:
            rows[-1]["close_seconds"] = close_seconds
    finally:
        if store is not None:
            store.close()
        if not args.keep_dbs:
            shutil.rmtree(path, ignore_errors=True)
    return rows


def build_parser(subparser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build argument parser for LSM compaction benchmark."""
    parser = subparser or argparse.ArgumentParser(description="Benchmark compaction-sensitive overwrite workload")
    parser.add_argument(
        "--configs",
        nargs="+",
        default=["leveldb", "rocksdb-p1-bg1-smallbuf", "rocksdb-p4-bg4-smallbuf", "rocksdb-p8-bg8-smallbuf", "rocksdb-p4-bg4-largebuf"],
    )
    parser.add_argument("--keys", type=int, default=250_000)
    parser.add_argument("--passes", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--value-size", type=int, default=1024)
    parser.add_argument("--write-buffer-size", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--key-order", choices=["sequential", "permuted"], default="permuted")
    parser.add_argument("--disable-wal", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_results/rocksdb_compaction"))
    parser.add_argument("--tmp-dir", type=Path, default=None)
    parser.add_argument("--keep-dbs", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    writer = ResultWriter(
        output_dir=args.output_dir,
        csv_filename="compaction_pressure_results.csv",
        jsonl_filename="compaction_pressure_results.jsonl",
        fieldnames=CSV_FIELDS,
    )
    for config in args.configs:
        rows = run_config(config, args)
        writer.write_rows(rows)


if __name__ == "__main__":
    main()
