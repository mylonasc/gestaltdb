"""Database lifecycle: identity, manifests, and backend resolution.

Database creation and opening stay on ``GraphDB`` (public constructors);
this module owns the manifest assembly/validation, the stable database-ID
protocol (filesystem lock plus backend metadata), and backend/serializer
class resolution. ``MANIFEST_FILENAME`` and friends are re-exported from
``gestaltdb.graphdb`` for existing importers.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from .registry import backend_registry, serializer_registry

MANIFEST_FILENAME = "gestaltdb_manifest.json"
MANIFEST_FORMAT_VERSION = 1
MANIFEST_METADATA_KEY = b"schema:manifest"

DATABASE_ID_LOCK = threading.Lock()


def backend_class(name: str):
    """Return the backend store class for a manifest name."""
    registry = backend_registry()
    if name not in registry:
        allowed = ", ".join(sorted(registry))
        raise ValueError(f"unknown GestaltDB backend '{name}'. Expected one of: {allowed}")
    return registry[name]


def serializer_class(name: str):
    """Return the serializer class for a manifest name."""
    registry = serializer_registry()
    if name not in registry:
        allowed = ", ".join(sorted(registry))
        raise ValueError(f"unknown GestaltDB serializer '{name}'. Expected one of: {allowed}")
    return registry[name]


def read_manifest_file(path: Path) -> dict:
    """Read and version-check a root manifest file."""
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        raise ValueError("unsupported GestaltDB manifest format version")
    return manifest


def initialize_database_id(store: KVStore, store_path: Path | None,
                            process_lock: threading.Lock,
                            database_id_key: bytes, expected: str | None) -> str:
    """Persist or reconcile the database UUID under a filesystem lock."""
    if expected is not None:
        try:
            expected = str(uuid.UUID(expected))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid database identity in root manifest") from exc
    lock_handle = None
    with process_lock:
        if store_path is not None:
            lock_path = store_path / ".gestaltdb_identity.lock"
            lock_handle = lock_path.open("a+b")
            if os.name == "posix":
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            elif os.name == "nt":  # pragma: no cover - Windows-only
                import msvcrt

                lock_handle.write(b"\0")
                lock_handle.flush()
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - unsupported platform
                raise RuntimeError("database identity locking is unsupported on this platform")
        try:
            return initialize_database_id_locked(store, store_path, database_id_key, expected)
        finally:
            if lock_handle is not None:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                elif os.name == "nt":  # pragma: no cover - Windows-only
                    import msvcrt

                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                lock_handle.close()


def initialize_database_id_locked(store: KVStore, store_path: Path | None,
                                   database_id_key: bytes,
                                  expected: str | None) -> str:
    """Reconcile the persisted database UUID without taking the file lock."""
    try:
        payload = store.get_metadata(database_id_key)
    except NotImplementedError as exc:
        raise ValueError("the configured store cannot persist a database identity") from exc
    if payload is None:
        if expected is None and store_path is None:
            raise ValueError("cannot assign a database identity without a managed store path; call save_manifest(path=...)")
        candidate = expected or str(uuid.uuid4())
        store.put_metadata(database_id_key, candidate.encode("ascii"))
        payload = store.get_metadata(database_id_key)
    try:
        value = str(uuid.UUID(payload.decode("ascii")))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid persisted database identity") from exc
    if expected is not None and value != expected:
        raise ValueError("persisted database identity does not match root manifest")
    return value
