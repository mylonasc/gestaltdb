"""Temporal secondary-index state and entry builders (Phase 5 extraction).

Index generation naming, visibility checksums, freshness guards, and the
per-version exact/range entry encoding. ``GraphDB`` keeps thin wrappers;
the commit path and rebuild flows call them unchanged. The
``temporal_v1_*`` index family names and the index-state key live here;
nothing outside this package imports them.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..versioning import TemporalCorruptionError, canonical_json_bytes
from . import index_maintenance as _index_maintenance
from .temporal_keys import temporal_visible_key
from .temporal_sequence import read_temporal_sequence

INDEX_STATE_KEY = b"temporal:indexes:v1:state"
VERSION_INDEX = "temporal_v1_version"
LOGICAL_COMMIT_INDEX = "temporal_v1_logical_commit"
LOGICAL_VALID_INDEX = "temporal_v1_logical_valid"
VALID_END_INDEX = "temporal_v1_valid_end"
OPEN_END = b"g" * 16
SYSTEM_INDEX = "temporal_v1_system_commit"
EDGE_OUT_INDEX = "temporal_v1_edge_out"
EDGE_IN_INDEX = "temporal_v1_edge_in"
NODE_CATALOG_INDEX = "temporal_v1_node_catalog"
EDGE_OUT_CATALOG_INDEX = "temporal_v1_edge_out_catalog"
EDGE_IN_CATALOG_INDEX = "temporal_v1_edge_in_catalog"
CLAIM_CATALOG_INDEX = "temporal_v1_claim_catalog"
CLAIM_STATEMENT_INDEX = "temporal_v1_claim_statement"
CLAIM_DIMENSION_INDEX = "temporal_v1_claim_dimension"


def load_index_state(store: KVStore, state_key: bytes) -> dict | None:
    """Load and validate the temporal index state record."""
    payload = store.get_metadata(state_key)
    if payload is None:
        return None
    try:
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        if value.get("format_version") in {1, 2, 3, 4}:
            # Later temporal stages added catalogs and epistemic indexes.
            # Older derived indexes must be rebuilt before querying.
            return None
        if value.get("format_version") != 5:
            raise ValueError
        indexed = value["indexed_through_commit"]
        generation = value["active_generation"]
        checksum = value["visibility_checksum"]
        if isinstance(indexed, bool) or not isinstance(indexed, int) or indexed < 0:
            raise ValueError
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalCorruptionError("invalid temporal index state") from exc


def index_state_horizon(store: KVStore, state_key: bytes) -> int | None:
    """Return the indexed-through commit, or ``None`` when indexes are absent."""
    state = load_index_state(store, state_key)
    return None if state is None else state["indexed_through_commit"]


def index_state_generation(store: KVStore, state_key: bytes) -> int:
    """Return the active index generation (1 when no state is published)."""
    state = load_index_state(store, state_key)
    return 1 if state is None else state["active_generation"]


def index_name(index: str, generation: int) -> str:
    """Return the generation-qualified physical index name."""
    if generation == 0:
        return index
    return f"{index}:g{generation:016x}"


def visibility_checksum(store: KVStore, through_commit: int, *,
                        pending_marker=None) -> str:
    """Digest commit visibility markers over ``1..through_commit``."""
    markers = []
    for commit_id in range(1, through_commit + 1):
        marker = (
            pending_marker[1]
            if pending_marker is not None and pending_marker[0] == commit_id
            else store.get_metadata(temporal_visible_key(commit_id))
        )
        if marker is not None:
            markers.append(commit_id.to_bytes(8, "big") + marker)
    return hashlib.sha256(b"".join(markers)).hexdigest()


def persist_index_state(store: KVStore, state_key: bytes, commit_id: int,
                        generation: int, *, pending_marker=None) -> None:
    """Publish the temporal index state record."""
    store.put_metadata(
        state_key,
        canonical_json_bytes({
            "format_version": 5,
            "active_generation": generation,
            "indexed_through_commit": commit_id,
            "visibility_checksum": visibility_checksum(
                store, commit_id, pending_marker=pending_marker
            ),
        }),
    )


def ensure_temporal_indexes(store: KVStore, state_key: bytes, stale_key: bytes,
                            horizon: int) -> None:
    """Fail closed when temporal indexes are stale or checksums diverge."""
    _index_maintenance.ensure_indexes_current(store, stale_key, "temporal")
    indexed = index_state_horizon(store, state_key)
    if horizon == 0 and indexed is None:
        return
    if indexed is None or indexed < horizon:
        raise RuntimeError("stale indexes require rebuild before query: temporal")
    state = load_index_state(store, state_key)
    if state["visibility_checksum"] != visibility_checksum(store, indexed):
        raise TemporalCorruptionError("temporal index generation visibility checksum mismatch")


def has_visible_temporal_history(store: KVStore, sequence_key: bytes,
                                 store_path, sequence_filename: str) -> bool:
    """Return whether any visible commit marker exists."""
    last_commit_id, _ = read_temporal_sequence(store, sequence_key, store_path, sequence_filename)
    return any(
        store.get_metadata(temporal_visible_key(commit_id)) is not None
        for commit_id in range(1, last_commit_id + 1)
    )
