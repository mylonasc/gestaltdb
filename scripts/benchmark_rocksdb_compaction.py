#!/usr/bin/env python3
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
import csv
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gestaltdb.kvstores import LevelDBStore, PyRexStore


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


def file_stats(path: Path) -> dict[str, int]:
    stats = {
        "sst_files": 0,
        "sst_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "total_files": 0,
        "total_bytes": 0,
    }
    for root, _, files in os.walk(path):
        for filename in files:
            file_path = Path(root) / filename
            try:
                size = file_path.stat().st_size
            except FileNotFoundError:
                continue
            stats["total_files"] += 1
            stats["total_bytes"] += size
            if filename.endswith((".sst", ".ldb")):
                stats["sst_files"] += 1
                stats["sst_bytes"] += size
            elif filename.endswith(".log"):
                stats["log_files"] += 1
                stats["log_bytes"] += size
    return stats


def make_value(pass_index: int, value_size: int) -> bytes:
    prefix = f"pass={pass_index:04d}|".encode("ascii")
    if len(prefix) >= value_size:
        return prefix[:value_size]
    return prefix + (b"x" * (value_size - len(prefix)))


def key_for_position(position: int, keys: int, pass_index: int, key_order: str) -> int:
    if key_order == "sequential":
        return position
    if key_order == "permuted":
        return ((position * 1_000_003) + (pass_index * 9_176)) % keys
    raise ValueError(f"unknown key order: {key_order}")


def write_pass(store, backend: str, keys: int, batch_size: int, pass_index: int, value_size: int, key_order: str) -> float:
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


def config_options(config: str, args: argparse.Namespace) -> tuple[str, dict[str, object]]:
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


def run_config(config: str, args: argparse.Namespace) -> list[dict[str, object]]:
    backend, options = config_options(config, args)
    if backend == "rocksdb" and args.disable_wal:
        options["disable_wal"] = True
    path = Path(tempfile.mkdtemp(prefix=f"gestaltdb_compaction_{config}_", dir=args.tmp_dir))
    rows: list[dict[str, object]] = []
    store = None
    close_seconds = 0.0
    try:
        store = PyRexStore(path=str(path), **options) if backend == "rocksdb" else LevelDBStore(path=str(path))
        for pass_index in range(args.passes):
            seconds = write_pass(store, backend, args.keys, args.batch_size, pass_index, args.value_size, args.key_order)
            stats = file_stats(path)
            row = {
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
                "seconds": seconds,
                "writes_per_second": args.keys / seconds,
                "mb_per_second": (args.keys * args.value_size / 1_000_000) / seconds,
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


def write_rows(output_dir: Path, rows: list[dict[str, object]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "compaction_pressure_results.jsonl"
    csv_path = output_dir / "compaction_pressure_results.csv"
    write_header = not csv_path.exists()
    with jsonl_path.open("a", encoding="utf-8") as jsonl, csv_path.open("a", newline="", encoding="utf-8") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            jsonl.write(json.dumps(row, sort_keys=True) + "\n")
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark compaction-sensitive overwrite workload")
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for config in args.configs:
        rows = run_config(config, args)
        write_rows(args.output_dir, rows)


if __name__ == "__main__":
    main()
