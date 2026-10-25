"""Temporal entity write conveniences (Phase 5 extraction, write side).

One-shot version assertion/correction/retraction helpers and claim
writes compose version-write descriptors with the transaction-aware
``commit_fn``. Legacy ``TimeIndexedEdge`` migration scans the current
store, validates payloads, and appends equivalent edge history.

The writer guard and transaction dispatch stay on ``GraphDB``.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ..temporal import TemporalInstant, TemporalInterval
from ..versioning import (
    ClaimVersion,
    ClaimVersionWrite,
    EdgeVersion,
    EdgeVersionWrite,
    NodeVersion,
    NodeVersionWrite,
    TemporalCorruptionError,
    TemporalVersionError,
    VersionOperation,
    normalize_logical_id,
    normalize_temporal_instant,
    normalize_version_id,
)
from .edge_records import get_edge
from .entities import Edge, Node, TimeIndexedEdge
from .temporal_reads import TemporalReader, claim_valid_interval
from .temporal_selection import _UNSET


def put_node_version(commit_fn, node: Node, *, valid,
                     version_id=None, metadata=None, index_mode="maintain"):
    """Append an immutable node assertion without changing the current graph."""
    commit = commit_fn(
        [NodeVersionWrite.assertion(node, valid, version_id=version_id)],
        metadata=metadata, index_mode=index_mode,
    )
    return commit.versions[0]


def put_edge_version(commit_fn, edge: Edge, *, valid,
                     version_id=None, metadata=None, index_mode="maintain"):
    """Append an immutable edge assertion without changing the current graph."""
    commit = commit_fn(
        [EdgeVersionWrite.assertion(edge, valid, version_id=version_id)],
        metadata=metadata, index_mode=index_mode,
    )
    return commit.versions[0]


def correct_node_version(commit_fn, node: Node, *,
                         supersedes_version_id: str, valid=None, version_id=None,
                         metadata=None, index_mode="maintain"):
    """Append a corrected node payload while preserving the superseded version."""
    commit = commit_fn([NodeVersionWrite.correction(
        node,
        supersedes_version_id=supersedes_version_id,
        valid=valid,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def correct_edge_version(commit_fn, edge: Edge, *,
                         supersedes_version_id: str, valid=None, version_id=None,
                         metadata=None, index_mode="maintain"):
    """Append a corrected edge payload while preserving the superseded version."""
    commit = commit_fn([EdgeVersionWrite.correction(
        edge,
        supersedes_version_id=supersedes_version_id,
        valid=valid,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def retract_node_version(commit_fn, logical_id: str, *, valid=None,
                         valid_from=None, supersedes_version_id=None,
                         reason=None, version_id=None, metadata=None,
                         index_mode="maintain"):
    """Append a node retraction over an explicit or inherited interval."""
    commit = commit_fn([NodeVersionWrite.retraction(
        logical_id,
        valid=valid,
        valid_from=valid_from,
        supersedes_version_id=supersedes_version_id,
        reason=reason,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def retract_edge_version(commit_fn, logical_id: str, *, valid=None,
                         valid_from=None, supersedes_version_id=None,
                         reason=None, version_id=None, metadata=None,
                         index_mode="maintain"):
    """Append an edge retraction over an explicit or inherited interval."""
    commit = commit_fn([EdgeVersionWrite.retraction(
        logical_id,
        valid=valid,
        valid_from=valid_from,
        supersedes_version_id=supersedes_version_id,
        reason=reason,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def assert_claim(commit_fn, *, subject, predicate, object, polarity, agent,
                 source=None, confidence=None, world="default", provenance=None,
                 object_kind=ClaimObjectKind.ENTITY, valid=None,
                 valid_from=None, valid_to=None, version_id=None,
                 metadata=None, index_mode="maintain"):
    """Append an immutable sourced assertion or denial."""
    interval = claim_valid_interval(valid, valid_from, valid_to, required=True)
    claim = Claim(
        subject=subject,
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        polarity=polarity,
        agent=agent,
        source=source,
        confidence=confidence,
        world=world,
        provenance={} if provenance is None else provenance,
    )
    commit = commit_fn(
        [ClaimVersionWrite.assertion(claim, interval, version_id=version_id)],
        metadata=metadata,
        index_mode=index_mode,
    )
    return commit.versions[0]


def correct_claim(reader: TemporalReader, commit_fn, claim, *,
                  supersedes_version_id, confidence=_UNSET, provenance=_UNSET,
                  valid=None, valid_from=None, valid_to=None, version_id=None,
                  metadata=None, index_mode="maintain"):
    """Append corrected confidence/provenance for one claim identity.

    Explicit confidence/provenance keywords override the supplied Claim's
    values, or the superseded version's values when given a claim ID.
    Omitted keywords preserve those values; the claim identity cannot change.
    """
    target = reader.get_version(normalize_version_id(supersedes_version_id))
    if not isinstance(target, ClaimVersion) or target.claim is None:
        raise TemporalVersionError("superseded claim version does not exist")
    if isinstance(claim, Claim):
        payload = claim.to_dict()
    else:
        claim_id = normalize_logical_id(claim)
        if claim_id != target.logical_id:
            raise TemporalVersionError("superseded claim version has a different claim ID")
        payload = target.claim.to_dict()
    if confidence is not _UNSET:
        payload["confidence"] = confidence
    if provenance is not _UNSET:
        payload["provenance"] = provenance
    corrected = Claim.from_dict(payload)
    if corrected.claim_id != target.logical_id:
        raise TemporalVersionError("claim corrections cannot change epistemic identity")
    interval = claim_valid_interval(valid, valid_from, valid_to, required=False)
    commit = commit_fn([ClaimVersionWrite.correction(
        corrected,
        supersedes_version_id=supersedes_version_id,
        valid=interval,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def retract_claim(commit_fn, claim_id, *, valid=None, valid_from=None,
                  valid_to=None, supersedes_version_id=None, reason=None,
                  version_id=None, metadata=None, index_mode="maintain"):
    """Append a bitemporal retraction without deleting claim history."""
    interval = claim_valid_interval(valid, valid_from, valid_to, required=False)
    commit = commit_fn([ClaimVersionWrite.retraction(
        claim_id,
        valid=interval,
        supersedes_version_id=supersedes_version_id,
        reason=reason,
        version_id=version_id,
    )], metadata=metadata, index_mode=index_mode)
    return commit.versions[0]


def migrate_time_indexed_edges(store: KVStore, entity_serializer,
                               reader: TemporalReader,
                               commit_fn, delete_edge_fn, *, metadata=None,
                               index_mode="maintain", delete_legacy=True):
    """Append equivalent edge history for legacy timestamp-prefixed edges."""
    grouped: dict[str, list[tuple[bytes, TimeIndexedEdge, TemporalInstant]]] = {}
    for raw_key in store.get_edge_keys_generator():
        try:
            edge = get_edge(store, entity_serializer, raw_key)
        except Exception as exc:
            raise TemporalCorruptionError(
                f"cannot decode legacy edge record at key {raw_key!r}"
            ) from exc
        if not isinstance(edge, TimeIndexedEdge):
            continue
        try:
            instant = normalize_temporal_instant(edge.timestamp_dat)
            expected_key = edge.get_id_bytes
        except (TypeError, ValueError, OverflowError, struct.error) as exc:
            raise TemporalCorruptionError(
                f"legacy TimeIndexedEdge '{edge.get_id}' has an invalid timestamp"
            ) from exc
        if raw_key != expected_key:
            raise TemporalCorruptionError(
                f"legacy TimeIndexedEdge '{edge.get_id}' key does not match its payload"
            )
        grouped.setdefault(edge.get_id, []).append((raw_key, edge, instant))

    existing_migration_versions: dict[str, list[EdgeVersion]] = {}
    migration_exists = False
    for commit in reader.iter_commits():
        if commit.metadata.get("migration") != "TimeIndexedEdge":
            continue
        migration_exists = True
        for version in commit.versions:
            if isinstance(version, EdgeVersion):
                existing_migration_versions.setdefault(version.logical_id, []).append(version)

    unseen_logical_ids = sorted(set(grouped) - set(existing_migration_versions))
    if migration_exists and unseen_logical_ids:
        raise TemporalVersionError(
            "legacy TimeIndexedEdge input contains new logical IDs after migration: "
            + ", ".join(unseen_logical_ids)
        )

    writes: list[EdgeVersionWrite] = []
    migrated_keys: list[bytes] = []
    for logical_id in sorted(grouped):
        records = sorted(grouped[logical_id], key=lambda item: (item[2].epoch_microseconds, item[0]))
        starts = [item[2].epoch_microseconds for item in records]
        if len(starts) != len(set(starts)):
            raise TemporalVersionError(
                f"legacy TimeIndexedEdge '{logical_id}' has duplicate timestamps"
            )
        existing_versions = existing_migration_versions.get(logical_id, [])
        existing_assertions = [
            version for version in existing_versions
            if version.operation is VersionOperation.ASSERT and version.edge is not None
        ]
        for index, (raw_key, edge, start) in enumerate(records):
            end = records[index + 1][2] if index + 1 < len(records) else None
            plain_edge = Edge(
                edge_id=edge.get_id,
                source=edge.source,
                target=edge.target,
                properties=edge.properties,
            )
            valid = TemporalInterval(start, end)
            matching_version = any(
                version.operation is VersionOperation.ASSERT
                and version.valid.start == start
                and version.edge is not None
                and version.edge.to_dict() == plain_edge.to_dict()
                for version in existing_assertions
            )
            if existing_versions and not matching_version:
                raise TemporalVersionError(
                    f"legacy TimeIndexedEdge '{logical_id}' changed after migration"
                )
            if not existing_versions:
                writes.append(EdgeVersionWrite.assertion(plain_edge, valid))
            migrated_keys.append(raw_key)

    versions: tuple[EdgeVersion, ...] = ()
    if writes:
        migration_metadata = {
            "migration": "TimeIndexedEdge",
            "interval_policy": "successive",
        }
        if metadata is not None:
            migration_metadata["user"] = dict(metadata)
        commit = commit_fn(
            writes,
            metadata=migration_metadata,
            index_mode=index_mode,
        )
        versions = tuple(commit.versions)

    if delete_legacy:
        for raw_key in migrated_keys:
            delete_edge_fn(raw_key)
    return versions
