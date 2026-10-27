"""Database lifecycle: identity, manifests, and backend resolution.

Database creation and opening stay on ``GraphDB`` (public constructors);
this module owns the manifest assembly/validation, the stable database-ID
protocol (filesystem lock plus backend metadata), and backend/serializer
class resolution. ``MANIFEST_FILENAME`` and friends are re-exported from
``gestaltdb.graphdb`` for existing importers.
"""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..kvstores import KVStore

from .registry import backend_registry, gestaltdb_version, serializer_registry, utc_now_iso

MANIFEST_FILENAME = "gestaltdb_manifest.json"
MANIFEST_FORMAT_VERSION = 1
MANIFEST_METADATA_KEY = b"schema:manifest"

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
                            lock_manager,
                            database_id_key: bytes, expected: str | None) -> str:
    """Persist or reconcile the database UUID under a filesystem lock."""
    if expected is not None:
        try:
            expected = str(uuid.UUID(expected))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid database identity in root manifest") from exc
    guard = (nullcontext() if store_path is None else
             lock_manager.for_path(store_path / ".gestaltdb_identity.lock"))
    with guard:
        return initialize_database_id_locked(store, store_path, database_id_key, expected)


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
        value = str(uuid.UUID(cast(bytes, payload).decode("ascii")))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid persisted database identity") from exc
    if expected is not None and value != expected:
        raise ValueError("persisted database identity does not match root manifest")
    return value


def manifest_from_current(store, *, backend_name, serializer_name, database_id,
                          indexed_node_properties, indexed_edge_properties,
                          created_at=None):
    """Assemble the self-describing manifest for the current configuration."""
    if backend_name is None or serializer_name is None:
        raise ValueError("cannot build manifest for unknown backend or serializer")
    backend_cls = backend_class(backend_name)
    serializer_cls = serializer_class(serializer_name)
    backend_options = {}
    if backend_name == "pyrex" and getattr(store, "transactional", False):
        backend_options["transactional"] = True
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "database_id": database_id,
        "gestaltdb_version": gestaltdb_version(),
        "backend": {
            "name": backend_name,
            "class": f"{backend_cls.__module__}.{backend_cls.__name__}",
            "layout_version": 1,
            "options": backend_options,
        },
        "serializer": {
            "name": serializer_name,
            "class": f"{serializer_cls.__module__}.{serializer_cls.__name__}",
            "format_version": 1,
        },
        "graph": {
            "node_count": None,
            "edge_count": None,
            "indexed_node_properties": list(sorted(indexed_node_properties)),
            "indexed_edge_properties": list(sorted(indexed_edge_properties)),
            "temporal_versions_format": 1,
        },
        "created_at": created_at or utc_now_iso(),
    }


def validate_backend_manifest(store, manifest_key: bytes, database_id: str | None,
                              root_manifest: dict) -> None:
    """Check backend metadata against the root manifest identity."""
    try:
        payload = store.get_metadata(manifest_key)
    except NotImplementedError:
        return
    if not payload:
        return
    backend_manifest = json.loads(payload.decode("utf-8"))
    if backend_manifest.get("backend", {}).get("name") != root_manifest.get("backend", {}).get("name"):
        raise ValueError("GestaltDB manifest/backend metadata mismatch for backend name")
    if backend_manifest.get("serializer", {}).get("name") != root_manifest.get("serializer", {}).get("name"):
        raise ValueError("GestaltDB manifest/backend metadata mismatch for serializer name")
    root_database_id = root_manifest.get("database_id")
    backend_database_id = backend_manifest.get("database_id")
    if root_database_id is not None and backend_database_id is not None and root_database_id != backend_database_id:
        raise ValueError("GestaltDB manifest/backend metadata mismatch for database identity")
    if root_database_id is not None and root_database_id != database_id:
        raise ValueError("root manifest does not match persisted database identity")
    if backend_database_id is not None and backend_database_id != database_id:
        raise ValueError("backend manifest does not match persisted database identity")
