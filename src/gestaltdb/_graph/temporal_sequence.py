"""Temporal commit sequencing (Phase 5 extraction, writer side).

The durable commit sequence lives in backend metadata with a filesystem
sidecar for crash safety. These functions read and reserve sequence values
given an explicit store, path configuration, and clock; the writer guard
that serializes reservation stays on ``GraphDB``.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..temporal import TemporalInstant
from ..versioning import TemporalCorruptionError, TemporalVersionError, canonical_json_bytes
from .temporal_types import Clock


def decode_temporal_sequence(payload: bytes) -> tuple[int, TemporalInstant]:
    """Decode a sequence payload into ``(commit_id, system_time)``."""
    try:
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError
        commit_id = decoded["commit_id"]
        system_time_us = decoded["system_time_us"]
        if isinstance(commit_id, bool) or not isinstance(commit_id, int):
            raise TypeError
        if isinstance(system_time_us, bool) or not isinstance(system_time_us, int):
            raise TypeError
        system_time = TemporalInstant(system_time_us)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalCorruptionError("invalid temporal commit sequence") from exc
    if commit_id < 0:
        raise TemporalCorruptionError("invalid temporal commit sequence")
    return commit_id, system_time


def read_temporal_sequence(store: KVStore, key: bytes, store_path: Path | None,
                           sequence_filename: str) -> tuple[int, TemporalInstant | None]:
    """Return the latest durable ``(commit_id, system_time)`` pair."""
    try:
        payload = store.get_metadata(key)
    except NotImplementedError as exc:
        raise TemporalVersionError("the configured store does not support temporal metadata") from exc
    stored = (0, None) if payload is None else decode_temporal_sequence(payload)
    if store_path is None:
        return stored
    sequence_path = store_path / sequence_filename
    try:
        sidecar_payload = sequence_path.read_bytes()
    except FileNotFoundError:
        return stored
    sidecar = decode_temporal_sequence(sidecar_payload)
    if sidecar[0] < stored[0]:
        raise TemporalCorruptionError("durable temporal sequence trails stored sequence")
    return sidecar


def next_temporal_system_time(clock: Clock,
                              previous: TemporalInstant | None) -> TemporalInstant:
    """Return a system time strictly after ``previous`` (microsecond bump)."""
    current = clock()
    if isinstance(current, TemporalInstant):
        instant = current
    else:
        instant = TemporalInstant.from_datetime(current)
    if previous is not None and instant <= previous:
        return TemporalInstant(previous.epoch_microseconds + 1)
    return instant


def reserve_temporal_sequence(store: KVStore, key: bytes, store_path: Path | None,
                              sequence_filename: str,
                              clock: Clock) -> tuple[int, TemporalInstant]:
    """Reserve the next commit ID and persist the sequence durably."""
    previous_commit_id, previous_system_time = read_temporal_sequence(
        store, key, store_path, sequence_filename)
    commit_id = previous_commit_id + 1
    if commit_id >= 1 << 64:
        raise TemporalVersionError("temporal commit ID space is exhausted")
    system_time = next_temporal_system_time(clock, previous_system_time)
    payload = canonical_json_bytes({
        "commit_id": commit_id,
        "system_time_us": system_time.epoch_microseconds,
    })
    if store_path is None:
        store.put_metadata(key, payload)
        return commit_id, system_time

    sequence_path = store_path / sequence_filename
    temporary_path = store_path / f"{sequence_filename}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, sequence_path)
        if os.name == "posix":
            directory_fd = os.open(store_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return commit_id, system_time
