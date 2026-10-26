"""Temporal storage key codecs (Phase 5 extraction, first step).

Pure commit/visibility/record key builders, opaque index locators, and
sortable instant encodings. No store access. ``GraphDB`` keeps thin static
wrappers so existing call sites — including reasoning adapters and
``readview`` — are untouched. The key-prefix constants live here and are
re-exported from ``gestaltdb.graphdb`` for backward compatibility.
"""

from __future__ import annotations

from ..temporal import TemporalInstant
from ..versioning import TemporalCorruptionError

_TEMPORAL_PREFIX = b"temporal:v1:"
_TEMPORAL_COMMIT_PREFIX = _TEMPORAL_PREFIX + b"commit:"
_TEMPORAL_RECORD_PREFIX = _TEMPORAL_PREFIX + b"record:"
_TEMPORAL_VISIBLE_PREFIX = _TEMPORAL_PREFIX + b"visible:"
_TEMPORAL_SEQUENCE_KEY = _TEMPORAL_PREFIX + b"sequence"
_TEMPORAL_SEQUENCE_FILENAME = ".gestaltdb_temporal_sequence"


def temporal_commit_key(commit_id: int) -> bytes:
    """Return the metadata key for a commit descriptor."""
    return _TEMPORAL_COMMIT_PREFIX + int(commit_id).to_bytes(8, "big", signed=False)


def temporal_visible_key(commit_id: int) -> bytes:
    """Return the metadata key for a commit visibility marker."""
    return _TEMPORAL_VISIBLE_PREFIX + int(commit_id).to_bytes(8, "big", signed=False)


def temporal_record_key(commit_id: int, ordinal: int) -> bytes:
    """Return the metadata key for one version record in a commit."""
    return (
        _TEMPORAL_RECORD_PREFIX
        + int(commit_id).to_bytes(8, "big", signed=False)
        + int(ordinal).to_bytes(4, "big", signed=False)
    )


def temporal_locator(commit_id: int, ordinal: int) -> bytes:
    """Return the opaque index locator for a version record."""
    return f"{commit_id:016x}{ordinal:08x}".encode("ascii")


def decode_temporal_locator(locator: bytes) -> tuple[int, int]:
    """Decode an opaque index locator into ``(commit_id, ordinal)``."""
    try:
        if not isinstance(locator, bytes) or len(locator) != 24:
            raise ValueError
        return int(locator[:16], 16), int(locator[16:], 16)
    except (TypeError, ValueError) as exc:
        raise TemporalCorruptionError("invalid temporal index locator") from exc


def temporal_instant_index_value(instant: TemporalInstant) -> bytes:
    """Return the sortable index encoding for a temporal instant."""
    return instant.encode_sortable().hex().encode("ascii")
