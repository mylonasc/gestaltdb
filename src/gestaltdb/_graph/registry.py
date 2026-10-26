"""Backend/serializer registries and manifest helpers (Phase 3 extraction).

These pure helpers map configured names to implementations and stamp
manifest metadata. They perform no I/O. ``GraphDB.create/open`` and
``__init__`` remain the public lifecycle API and delegate here.
"""

from __future__ import annotations

import datetime
from importlib import metadata as importlib_metadata


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string without microseconds."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def gestaltdb_version() -> str | None:
    """Return the installed package version, or ``None`` when unknown."""
    try:
        return importlib_metadata.version("gestaltdb")
    except importlib_metadata.PackageNotFoundError:
        return None


def backend_registry():
    """Map backend names to store implementations (lazy imports)."""
    from ..kvstores import LMDBStore, LevelDBStore, PyRexStore

    return {
        "lmdb": LMDBStore,
        "leveldb": LevelDBStore,
        "pyrex": PyRexStore,
    }


def serializer_registry():
    """Map serializer names to implementations (lazy imports)."""
    from ..serializers import JSONSerializer, MessagePackSerializer, PickleSerializer, ProtobufSerializer

    return {
        "pickle": PickleSerializer,
        "json": JSONSerializer,
        "messagepack": MessagePackSerializer,
        "protobuf": ProtobufSerializer,
    }


def registry_name_for_instance(instance, registry: dict[str, type]) -> str | None:
    """Return the registry name for an instance, or ``None`` when unknown."""
    for name, cls in registry.items():
        if isinstance(instance, cls):
            return name
    return None
