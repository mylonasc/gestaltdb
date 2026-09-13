"""Database opening, serializer factories, storage presets, and dependency checkers."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore, PyRexStore
from gestaltdb.serializers import (
    JSONSerializer,
    MessagePackSerializer,
    PickleSerializer,
    ProtobufSerializer,
    Serializer,
)


def serializer_factory(name: str) -> Serializer:
    """Return an instantiated serializer by name."""
    norm = name.lower()
    if norm == "pickle":
        return PickleSerializer()
    if norm == "msgpack":
        return MessagePackSerializer()
    if norm == "json":
        return JSONSerializer()
    if norm == "protobuf":
        return ProtobufSerializer()
    raise ValueError(f"unknown serializer: {name}")


def open_gestaltdb(
    path: Path | str,
    backend: str = "rocksdb",
    serializer: str | Serializer = "json",
    rocksdb_options: dict[str, Any] | None = None,
    map_size: int = 2**34,
) -> GraphDB:
    """Open and return a GraphDB instance for benchmarking."""
    path_str = str(path)
    ser_obj = serializer_factory(serializer) if isinstance(serializer, str) else serializer
    backend_lower = backend.lower()

    if backend_lower == "leveldb":
        return GraphDB(LevelDBStore(path=path_str), ser_obj)

    if backend_lower == "rocksdb":
        opts = rocksdb_options or {}
        return GraphDB(PyRexStore(path=path_str, **opts), ser_obj)

    if backend_lower == "lmdb":
        try:
            from gestaltdb.kvstores import LMDBStore
        except ImportError as exc:
            raise RuntimeError("lmdb is not installed or available") from exc
        return GraphDB(LMDBStore(path=path_str, map_size=map_size), ser_obj)

    raise ValueError(f"unknown backend: {backend}")


def rocksdb_configs(selected: list[str], cores: int = 1) -> list[tuple[str, dict[str, Any]]]:
    """Generate RocksDB option presets parameterized by core count."""
    configs: dict[str, dict[str, Any]] = {
        "default": {},
        "transactional": {"transactional": True},
        "parallel": {"parallelism": cores, "max_background_jobs": cores},
        "parallel-transactional": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "transactional": True,
        },
        "parallel-buffer64mb-bloom10": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "write_buffer_size": 64 * 1024 * 1024,
            "bloom_bits_per_key": 10,
        },
        "parallel-buffer64mb-bloom10-transactional": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "write_buffer_size": 64 * 1024 * 1024,
            "bloom_bits_per_key": 10,
            "transactional": True,
        },
        "parallel-buffer64mb-bloom10-nowal": {
            "parallelism": cores,
            "max_background_jobs": cores,
            "write_buffer_size": 64 * 1024 * 1024,
            "bloom_bits_per_key": 10,
            "disable_wal": True,
        },
    }
    return [(name, configs[name]) for name in selected if name in configs]


def disk_usage(path: Path | str) -> int:
    """Calculate total recursive disk usage in bytes for a directory or file."""
    p = Path(path)
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except FileNotFoundError:
                pass
    return total


def file_stats(path: Path | str) -> dict[str, int]:
    """Calculate counts and sizes of SST, log, and total files in an LSM directory."""
    p = Path(path)
    stats = {
        "sst_files": 0,
        "sst_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "total_files": 0,
        "total_bytes": 0,
    }
    if not p.exists():
        return stats
    for root, _, files in os.walk(p):
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


def has_module(name: str) -> bool:
    """Check whether a Python module is importable."""
    return importlib.util.find_spec(name) is not None


def has_pyrex() -> bool:
    return has_module("pyrex")


def has_plyvel() -> bool:
    return has_module("plyvel")


def has_lmdb() -> bool:
    return has_module("lmdb")


def has_pyarrow() -> bool:
    return has_module("pyarrow")


def has_polars() -> bool:
    return has_module("polars")


def validate_matrix_dependencies(backend: str, ingestion_mode: str, serializer: str) -> str | None:
    """Validate dependency availability for matrix combinations."""
    if backend == "leveldb" and not has_plyvel():
        return "missing plyvel"
    if backend == "rocksdb" and not has_pyrex():
        return "missing pyrex-rocksdb"
    if backend == "lmdb" and not has_lmdb():
        return "missing lmdb"
    if ingestion_mode == "arrow" and not has_pyarrow():
        return "missing pyarrow"
    if ingestion_mode == "polars" and not has_polars():
        return "missing polars"
    if serializer == "msgpack" and not has_module("msgpack"):
        return "missing msgpack"
    if serializer == "protobuf" and not has_module("google.protobuf"):
        return "missing protobuf"
    if serializer == "json" and ingestion_mode == "object":
        return "json cannot serialize legacy adjacency bytes written by object ingestion"
    return None
