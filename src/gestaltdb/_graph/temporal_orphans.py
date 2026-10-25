"""Temporal orphan audit and reclamation (Phase 5 extraction, recovery side).

Durable allocation gaps and incomplete unpublished commits are inspected
and removed under the writer guard. ``list_temporal_orphans`` and
``reclaim_temporal_orphans`` keep the guard (plus transaction dispatch for
reclaim); the scan and cleanup cores are explicit store+reader functions.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..temporal import TemporalInstant
from ..versioning import (
    TemporalCorruptionError,
    TemporalOrphanArtifact,
    TemporalOrphanReclaimResult,
    TemporalOrphanReport,
    normalize_version_id,
)
from . import index_maintenance as _index_maintenance
from . import temporal_indexes as _temporal_indexes
from .temporal_keys import (
    temporal_commit_key,
    temporal_instant_index_value,
    temporal_record_key,
    temporal_visible_key,
)
from .temporal_reads import TemporalReader
from .temporal_sequence import read_temporal_sequence


def orphan_details(store: KVStore, decode_version, commit_id: int,
                   descriptor_payload: bytes):
    """Decode an unpublished commit descriptor and its present records."""
    try:
        descriptor = json.loads(descriptor_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalCorruptionError("invalid unpublished temporal commit descriptor") from exc
    if not isinstance(descriptor, dict):
        raise TemporalCorruptionError("unpublished temporal commit descriptor must be an object")
    if descriptor.get("format_version") != 1 or descriptor.get("commit_id") != commit_id:
        raise TemporalCorruptionError("invalid unpublished temporal commit descriptor")
    version_ids = descriptor.get("version_ids")
    record_digests = descriptor.get("record_digests")
    if (
        not isinstance(version_ids, list)
        or not version_ids
        or not all(isinstance(value, str) for value in version_ids)
        or len(set(version_ids)) != len(version_ids)
        or not isinstance(record_digests, list)
        or len(version_ids) != len(record_digests)
        or not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in record_digests
        )
    ):
        raise TemporalCorruptionError("invalid unpublished temporal commit record catalog")
    try:
        normalized_version_ids = [normalize_version_id(value) for value in version_ids]
        system_time_us = descriptor["system_time_us"]
        if isinstance(system_time_us, bool) or not isinstance(system_time_us, int):
            raise TypeError
        system_time = TemporalInstant(system_time_us)
    except (KeyError, TypeError, ValueError) as exc:
        raise TemporalCorruptionError("invalid unpublished temporal commit metadata") from exc
    if normalized_version_ids != version_ids:
        raise TemporalCorruptionError("unpublished temporal commit contains a non-canonical version ID")

    versions = []
    for ordinal, (version_id, expected_digest) in enumerate(zip(version_ids, record_digests)):
        envelope = store.get_metadata(temporal_record_key(commit_id, ordinal))
        if envelope is None:
            continue
        if hashlib.sha256(envelope).hexdigest() != expected_digest:
            raise TemporalCorruptionError("unpublished temporal commit record is corrupt")
        version = decode_version(envelope)
        if (
            version.version_id != version_id
            or version.commit_id != commit_id
            or version.commit_ordinal != ordinal
            or version.system_time != system_time
        ):
            raise TemporalCorruptionError("unpublished temporal record does not match its descriptor")
        versions.append(version)
    return descriptor, system_time, tuple(versions)


def list_orphans_locked(store: KVStore, reader: TemporalReader,
                        through_commit=None) -> TemporalOrphanReport:
    """Audit allocation gaps and incomplete commits up to a horizon."""
    allocation_horizon, _ = read_temporal_sequence(
        store, reader.sequence_key, reader.store_path, reader.sequence_filename)
    if through_commit is None:
        through_commit = allocation_horizon
    if (
        isinstance(through_commit, bool)
        or not isinstance(through_commit, int)
        or through_commit < 0
    ):
        raise ValueError("through_commit must be a non-negative integer")
    if through_commit > allocation_horizon:
        raise ValueError("through_commit is newer than the allocation horizon")

    artifacts = []
    for commit_id in range(1, through_commit + 1):
        marker = store.get_metadata(temporal_visible_key(commit_id))
        descriptor_payload = store.get_metadata(temporal_commit_key(commit_id))
        if marker is not None:
            reader.get_commit(commit_id)
            continue
        if descriptor_payload is None:
            artifacts.append(TemporalOrphanArtifact(
                commit_id=commit_id,
                kind="reservation_gap",
                system_time=None,
                expected_record_count=None,
                present_record_count=0,
            ))
            continue
        descriptor, system_time, versions = orphan_details(
            store, reader.decode_version, commit_id, descriptor_payload
        )
        artifacts.append(TemporalOrphanArtifact(
            commit_id=commit_id,
            kind="incomplete_commit",
            system_time=system_time,
            expected_record_count=len(descriptor["version_ids"]),
            present_record_count=len(versions),
        ))
    return TemporalOrphanReport(allocation_horizon, through_commit, tuple(artifacts))


def reclaim_orphans_locked(store: KVStore, reader: TemporalReader, stale_key: bytes,
                           rebuild_fn: Callable[[], object],
                           through_commit: int) -> TemporalOrphanReclaimResult:
    """Delete validated unpublished artifacts through an explicit horizon."""
    report = list_orphans_locked(store, reader, through_commit)
    incomplete = [
        artifact for artifact in report.artifacts
        if artifact.kind == "incomplete_commit"
    ]
    gaps = tuple(
        artifact.commit_id for artifact in report.artifacts
        if artifact.kind == "reservation_gap"
    )
    if not incomplete:
        return TemporalOrphanReclaimResult(
            report.allocation_horizon,
            report.through_commit,
            (),
            gaps,
            False,
        )

    _index_maintenance.mark_indexes_stale(store, stale_key, "temporal")
    cleanup = []
    for artifact in incomplete:
        descriptor_key = temporal_commit_key(artifact.commit_id)
        descriptor_payload = store.get_metadata(descriptor_key)
        if descriptor_payload is None:
            raise TemporalCorruptionError("temporal orphan changed during reclamation")
        descriptor, system_time, versions = orphan_details(
            store, reader.decode_version, artifact.commit_id, descriptor_payload
        )
        cleanup.append((artifact.commit_id, descriptor_key, descriptor, system_time, versions))

    generation = _temporal_indexes.index_state_generation(store, reader.state_key)
    for commit_id, _, _, system_time, versions in cleanup:
        for version in versions:
            exact_entries, range_entries = _temporal_indexes.temporal_index_entries(
                version, generation=generation)
            for index_name, parts, value in exact_entries:
                store.delete_index_entry(index_name, parts, value)
            for index_name, parts, range_value, value in range_entries:
                store.delete_range_index_entry(index_name, parts, range_value, value)
            store.delete_range_index_entry(
                _temporal_indexes.index_name(
                    _temporal_indexes.SYSTEM_INDEX, generation,
                ),
                [b"commit"],
                temporal_instant_index_value(system_time),
                f"{commit_id:016x}".encode("ascii"),
            )

    for commit_id, descriptor_key, descriptor, _, _ in cleanup:
        for ordinal in range(len(descriptor["version_ids"])):
            store.delete_metadata(temporal_record_key(commit_id, ordinal))
        store.delete_metadata(descriptor_key)

    rebuild_fn()
    return TemporalOrphanReclaimResult(
        report.allocation_horizon,
        report.through_commit,
        tuple(item[0] for item in cleanup),
        gaps,
        True,
    )
