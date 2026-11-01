"""Temporal commit reads and version lookups (Phase 5 extraction, read side).

``TemporalReader`` bundles exactly what the historical read path needs: the
backend store, the entity serializer, the handle-owned commit cache, and
the sequence/state/stale key configuration. It performs no writes and never
touches mutable graph records, indexes, or the writer guard.

The commit cache dict is owned by the calling ``GraphDB`` handle and shared
by design across transaction/snapshot scopes: entries are keyed by store
identity, so a snapshot-bound reader never confuses another scope's
records. ``GraphDB`` keeps thin wrappers (including the private names
reasoning adapters rely on) and constructs a reader bound to its own scope.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, cast

if TYPE_CHECKING:
    from ..kvstores import KVStore
    from ..readview import GraphReadView

from ..epistemic import Claim

from ..temporal import TemporalInstant, TemporalInterval, TemporalIntervalInput
from ..versioning import (
    ClaimVersion,
    EdgeVersion,
    NodeVersion,
    TemporalCommit,
    TemporalCorruptionError,
    TemporalVersionError,
    TemporalVersion,
    VersionOperation,
    canonical_json_bytes,
    decode_version_envelope,
    immutable_metadata,
    normalize_logical_id,
    normalize_temporal_instant,
    normalize_temporal_interval,
    normalize_version_id,
)
from . import temporal_indexes as _temporal_indexes
from . import index_maintenance as _index_maintenance
from .temporal_keys import (
    decode_temporal_locator,
    temporal_commit_key,
    temporal_instant_index_value,
    temporal_locator,
    temporal_record_key,
    temporal_visible_key,
)
from .temporal_sequence import read_temporal_sequence
from .entities import Edge, GraphEntityDictSerializer, Node
from .temporal_types import CommitCache, PointInput, TemporalReadSource, VersionFields, VersionT


def _commit_cache_bytes(cache: CommitCache) -> int:
    """Return stored descriptor plus retained record sizes (decoded commits share that floor)."""
    total = 0
    for _, descriptor_payload, sizes, _ in cache.values():
        total += len(descriptor_payload) + sum(sizes)
    return total


@dataclass
class TemporalReader:
    """Store-bound historical read capability for one graph scope."""

    store: KVStore
    entity_serializer: GraphEntityDictSerializer
    commit_cache: CommitCache
    sequence_key: bytes
    store_path: Path | None
    sequence_filename: str
    state_key: bytes
    stale_key: bytes

    # -- version decoding ---------------------------------------------

    def decode_version(self, envelope: bytes) -> TemporalVersion:
        """Decode one version envelope, verifying payload identity."""
        header, payload = decode_version_envelope(envelope)
        required = {
            "entity_kind", "version_id", "logical_id", "valid_from_us", "valid_to_us",
            "commit_id", "commit_ordinal", "system_time_us", "operation",
        }
        if not required.issubset(header):
            raise TemporalCorruptionError("temporal version header is incomplete")
        try:
            integer_fields = (
                header["valid_from_us"],
                header["commit_id"],
                header["commit_ordinal"],
                header["system_time_us"],
            )
            if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
                raise TypeError
            if header["valid_to_us"] is not None and (
                isinstance(header["valid_to_us"], bool) or not isinstance(header["valid_to_us"], int)
            ):
                raise TypeError
            valid = TemporalInterval.from_values(header["valid_from_us"], header["valid_to_us"])
            common: VersionFields = {
                "version_id": normalize_version_id(header["version_id"]),
                "logical_id": normalize_logical_id(header["logical_id"]),
                "valid": valid,
                "commit_id": header["commit_id"],
                "commit_ordinal": header["commit_ordinal"],
                "system_time": TemporalInstant(header["system_time_us"]),
                "operation": VersionOperation(header["operation"]),
                "payload_hash": header["payload_hash"],
                "supersedes_version_id": (
                    None
                    if header.get("supersedes_version_id") is None
                    else normalize_version_id(header["supersedes_version_id"])
                ),
                "reason": header.get("reason"),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalCorruptionError("invalid temporal version header values") from exc
        if not isinstance(common["payload_hash"], str):
            raise TemporalCorruptionError("temporal version payload hash is invalid")
        operation = common["operation"]
        if operation is VersionOperation.ASSERT and common["supersedes_version_id"] is not None:
            raise TemporalCorruptionError("temporal assertion unexpectedly supersedes another version")
        if operation is VersionOperation.CORRECT and common["supersedes_version_id"] is None:
            raise TemporalCorruptionError("temporal correction has no superseded version")
        if operation is not VersionOperation.RETRACT and common["reason"] is not None:
            raise TemporalCorruptionError("non-retraction temporal version has a reason")
        if common["reason"] is not None and (
            not isinstance(common["reason"], str) or not common["reason"]
        ):
            raise TemporalCorruptionError("temporal retraction reason is invalid")
        if operation is VersionOperation.RETRACT:
            if payload:
                raise TemporalCorruptionError("temporal retraction unexpectedly contains a payload")
            entity: Node | Edge | Claim | None = None
        elif not payload:
            raise TemporalCorruptionError("temporal assertion or correction has no payload")
        elif header["entity_kind"] == "node":
            try:
                entity = self.entity_serializer.deserialize(payload, "Node")
            except Exception as exc:
                raise TemporalCorruptionError("cannot decode temporal node payload") from exc
        elif header["entity_kind"] == "edge":
            try:
                entity = self.entity_serializer.deserialize(payload, "Edge")
            except Exception as exc:
                raise TemporalCorruptionError("cannot decode temporal edge payload") from exc
        elif header["entity_kind"] == "claim":
            try:
                entity = Claim.from_dict(json.loads(payload.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TemporalVersionError) as exc:
                raise TemporalCorruptionError("cannot decode temporal claim payload") from exc
        else:
            raise TemporalCorruptionError("unknown temporal entity kind")
        if entity is not None:
            entity_id = entity.claim_id if isinstance(entity, Claim) else entity.get_id
            if entity_id != common["logical_id"]:
                raise TemporalCorruptionError("temporal payload ID does not match its logical ID")
        if header["entity_kind"] == "node":
            return NodeVersion(**common, node=cast(Node | None, entity))
        if header["entity_kind"] == "edge":
            return EdgeVersion(**common, edge=cast(Edge | None, entity))
        if header["entity_kind"] == "claim":
            return ClaimVersion(**common, claim=cast(Claim | None, entity))
        raise TemporalCorruptionError("unknown temporal entity kind")

    @staticmethod
    def copy_payload(version: VersionT) -> VersionT:
        """Detach mutable entity payloads from a cached version."""
        if isinstance(version, NodeVersion) and version.node is not None:
            return cast(VersionT, replace(version, node=copy.deepcopy(version.node)))
        if isinstance(version, EdgeVersion) and version.edge is not None:
            return cast(VersionT, replace(version, edge=copy.deepcopy(version.edge)))
        # Claim and its recursively frozen provenance are immutable values.
        return version

    # -- commit reads -------------------------------------------------

    def get_commit(self, commit_id: int, *, _validate_supersession: bool = True) -> TemporalCommit | None:
        """Return a validated visible commit with detached mutable entities.

        Decode reuse is bounded and scoped to the exact store object. Pinned
        verifiable read snapshots can reuse validated records directly;
        live/unverifiable stores recheck descriptor, marker and record digests.
        """
        commit = self.get_commit_cached(commit_id, _validate_supersession=_validate_supersession)
        if commit is None:
            return None
        return replace(commit, versions=tuple(self.copy_payload(v) for v in commit.versions))

    def get_commit_cached(self, commit_id: int, *,
                          _validate_supersession: bool = True) -> TemporalCommit | None:
        """Return a cached validated commit, rechecking store markers."""
        if isinstance(commit_id, bool) or not isinstance(commit_id, int) or commit_id < 1:
            raise ValueError("commit_id must be a positive integer")
        cache_key = (id(self.store), commit_id, bool(_validate_supersession))
        cached = self.commit_cache.get(cache_key)
        identity = getattr(self.store, "snapshot_identity", None)
        if (
            cached is not None
            and cached[0] is self.store
            and identity is not None
            and identity.verifiable
            and identity.sequence is not None
        ):
            return cached[3]
        descriptor_payload = self.store.get_metadata(temporal_commit_key(commit_id))
        marker = self.store.get_metadata(temporal_visible_key(commit_id))
        if marker is None:
            self.commit_cache.pop(cache_key, None)
            return None
        if descriptor_payload is None or marker != hashlib.sha256(descriptor_payload).digest():
            raise TemporalCorruptionError("temporal commit marker does not match its descriptor")
        if cached is not None and cached[0] is self.store and cached[1] == descriptor_payload:
            # Descriptor unchanged; verify live envelopes against its digests
            # without retaining them. Cached versions stay valid; only
            # supersession targets can change on mutable stores.
            try:
                hit_catalog = json.loads(descriptor_payload.decode("utf-8"))
                hit_digests = hit_catalog.get("record_digests")
                if not isinstance(hit_digests, list) or not hit_digests:
                    raise ValueError
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError) as exc:
                raise TemporalCorruptionError("invalid temporal commit descriptor") from exc
            if len(hit_digests) != len(cached[2]):
                raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            for ordinal, expected_digest in enumerate(hit_digests):
                envelope = self.store.get_metadata(temporal_record_key(commit_id, ordinal))
                if envelope is None or hashlib.sha256(envelope).hexdigest() != expected_digest:
                    raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            cached_commit = cached[3]
            if not _validate_supersession or not any(
                v.supersedes_version_id for v in cached_commit.versions
            ):
                return cached_commit
            distinct_targets = list(
                dict.fromkeys(
                    version.supersedes_version_id
                    for version in cached_commit.versions
                    if version.supersedes_version_id is not None
                )
            )
            if distinct_targets:
                targets = self.get_versions_through_batch(distinct_targets, commit_id - 1)
                for version in cached_commit.versions:
                    if version.supersedes_version_id is None:
                        continue
                    target = targets[normalize_version_id(version.supersedes_version_id)]
                    if target is None:
                        raise TemporalCorruptionError("temporal version supersedes a missing version")
                    if type(target) is not type(version) or target.logical_id != version.logical_id:
                        raise TemporalCorruptionError("temporal supersession kind or logical ID mismatch")
            return cached_commit
        try:
            descriptor = json.loads(descriptor_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TemporalCorruptionError("invalid temporal commit descriptor") from exc
        if not isinstance(descriptor, dict):
            raise TemporalCorruptionError("temporal commit descriptor must be an object")
        if descriptor.get("format_version") != 1 or descriptor.get("commit_id") != commit_id:
            raise TemporalCorruptionError("invalid temporal commit descriptor")
        version_ids = descriptor.get("version_ids")
        record_digests = descriptor.get("record_digests")
        if not isinstance(version_ids, list) or not isinstance(record_digests, list) or len(version_ids) != len(record_digests):
            raise TemporalCorruptionError("invalid temporal commit record catalog")
        if not version_ids:
            raise TemporalCorruptionError("temporal commit contains no versions")
        if not all(isinstance(value, str) for value in version_ids):
            raise TemporalCorruptionError("temporal commit contains an invalid version ID")
        if len(set(version_ids)) != len(version_ids):
            raise TemporalCorruptionError("temporal commit contains duplicate version IDs")
        try:
            normalized_version_ids = [normalize_version_id(value) for value in version_ids]
        except (TypeError, ValueError) as exc:
            raise TemporalCorruptionError("temporal commit contains an invalid version ID") from exc
        if normalized_version_ids != version_ids:
            raise TemporalCorruptionError("temporal commit contains a non-canonical version ID")
        if not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in record_digests
        ):
            raise TemporalCorruptionError("temporal commit contains an invalid record digest")
        try:
            system_time_us = descriptor["system_time_us"]
            descriptor_metadata = descriptor.get("metadata", {})
            if isinstance(system_time_us, bool) or not isinstance(system_time_us, int):
                raise TypeError
            if not isinstance(descriptor_metadata, dict):
                raise TypeError
            system_time = TemporalInstant(system_time_us)
            metadata = immutable_metadata(descriptor_metadata)
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalCorruptionError("invalid temporal commit metadata") from exc
        versions: list[TemporalVersion] = []
        sizes: list[int] = []
        for ordinal, (version_id, expected_digest) in enumerate(zip(version_ids, record_digests)):
            envelope = self.store.get_metadata(temporal_record_key(commit_id, ordinal))
            if envelope is None or hashlib.sha256(envelope).hexdigest() != expected_digest:
                raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            sizes.append(len(envelope))
            version = self.decode_version(envelope)
            if version.version_id != version_id or version.commit_id != commit_id or version.commit_ordinal != ordinal:
                raise TemporalCorruptionError("temporal commit record does not match its descriptor")
            if version.system_time != system_time:
                raise TemporalCorruptionError("temporal version system time does not match its commit")
            versions.append(version)
        if _validate_supersession:
            distinct_targets = list(
                dict.fromkeys(
                    version.supersedes_version_id
                    for version in versions
                    if version.supersedes_version_id is not None
                )
            )
            if distinct_targets:
                targets = self.get_versions_through_batch(distinct_targets, commit_id - 1)
                for version in versions:
                    if version.supersedes_version_id is None:
                        continue
                    target = targets[normalize_version_id(version.supersedes_version_id)]
                    if target is None:
                        raise TemporalCorruptionError("temporal version supersedes a missing version")
                    if type(target) is not type(version) or target.logical_id != version.logical_id:
                        raise TemporalCorruptionError("temporal supersession kind or logical ID mismatch")
        commit = TemporalCommit(commit_id, system_time, metadata, tuple(versions))
        # Bounded reuse: entries and bytes. Commits are immutable so eviction
        # only costs a future re-decode, never correctness. Bytes cover the
        # stored descriptor plus retained record sizes; decoded commits share
        # that floor without retaining full envelopes.
        if len(self.commit_cache) >= 256 or _commit_cache_bytes(self.commit_cache) >= 64 * 1024 * 1024:
            # Drop the oldest half; commits are immutable so eviction only
            # costs a future re-decode, never correctness.
            for old_key in list(self.commit_cache)[:128]:
                del self.commit_cache[old_key]
        self.commit_cache[cache_key] = (self.store, descriptor_payload, tuple(sizes), commit)
        return commit

    def iter_commits(self, *, through_commit: int | None = None) -> Iterator[TemporalCommit]:
        """Iterate visible commits in commit order, skipping incomplete commits."""
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.sequence_key, self.store_path, self.sequence_filename)
        limit = last_commit_id if through_commit is None else min(last_commit_id, through_commit)
        for commit_id in range(1, limit + 1):
            commit = self.get_commit(commit_id)
            if commit is not None:
                yield commit

    # -- version lookups ----------------------------------------------

    def get_version(self, version_id: str) -> TemporalVersion | None:
        """Return the latest visible version with an ID, scanning all commits."""
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.sequence_key, self.store_path, self.sequence_filename)
        return self.get_version_through(version_id, last_commit_id)

    def get_version_at(self, locator: bytes) -> TemporalVersion | None:
        """Return the version addressed by an opaque index locator."""
        commit_id, ordinal = decode_temporal_locator(locator)
        commit = self.get_commit_cached(commit_id, _validate_supersession=False)
        if commit is None:
            return None
        if ordinal >= len(commit.versions):
            raise TemporalCorruptionError("temporal index locator ordinal is out of range")
        return self.copy_payload(commit.versions[ordinal])

    def _usable_version_index_generation(self, through_commit: int) -> int | None:
        """Return a usable version-index generation for a horizon, or None.

        This is an O(1) freshness gate: stale families and uncovered horizons
        fall back to one bounded canonical pass. It never repairs or publishes
        indexes. Checksum divergence is surfaced by explicit ``ensure`` scopes
        and per-locator commit authentication, not by a per-target checksum.
        """
        if through_commit < 1:
            return None
        if "temporal" in _index_maintenance.load_stale_indexes(self.store, self.stale_key):
            return None
        state = _temporal_indexes.load_index_state(self.store, self.state_key)
        if state is None:
            return None
        if state["indexed_through_commit"] < through_commit:
            return None
        return state["active_generation"]

    def get_versions_through_batch(
        self, version_ids: object, through_commit: int
    ) -> dict[str, TemporalVersion | None]:
        """Resolve distinct version IDs once against one captured horizon.

        When the published version index covers ``through_commit`` and is not
        stale, each distinct target is located via the index and then
        authenticated through its commit descriptor/digest, exact version ID,
        commit/ordinal agreement and visibility. A miss cannot prove
        nonexistence without coverage: index misses trigger one bounded
        canonical pass, and an omission despite claimed coverage raises
        corruption. Deferred or absent indexes use one bounded canonical pass
        per batch. This never repairs or publishes indexes and never reuses
        same-batch or future records: ``through_commit`` must be captured
        before the batch's own commit is published.
        """
        if isinstance(through_commit, bool) or not isinstance(through_commit, int):
            raise ValueError("through_commit must be a non-negative integer")
        if through_commit < 0:
            raise ValueError("through_commit must be a non-negative integer")
        if not isinstance(version_ids, (list, tuple, set, frozenset)):
            # Accept any iterable but materialize once for distinct handling.
            try:
                version_ids = tuple(version_ids)  # type: ignore[arg-type]
            except TypeError as exc:
                raise TypeError("version_ids must be an iterable of version IDs") from exc
        normalized: list[str] = [normalize_version_id(value) for value in version_ids]
        distinct: list[str] = list(dict.fromkeys(normalized))
        resolved: dict[str, TemporalVersion | None] = {value: None for value in distinct}
        if not distinct or through_commit < 1:
            return resolved
        generation = self._usable_version_index_generation(through_commit)
        if generation is not None:
            missing: set[str] = set()
            for value in distinct:
                locators = list(
                    self.store.iter_index_prefix(
                        _temporal_indexes.index_name(
                            _temporal_indexes.VERSION_INDEX, generation
                        ),
                        [value.encode("ascii")],
                    )
                )
                unique = set(locators)
                if not unique:
                    missing.add(value)
                    continue
                if len(unique) > 1:
                    raise TemporalCorruptionError(
                        "temporal version index contains duplicate locators"
                    )
                locator = next(iter(unique))
                commit_id, ordinal = decode_temporal_locator(locator)
                if commit_id < 1 or commit_id > through_commit:
                    missing.add(value)
                    continue
                commit = self.get_commit_cached(commit_id, _validate_supersession=False)
                if commit is None:
                    missing.add(value)
                    continue
                if ordinal >= len(commit.versions):
                    raise TemporalCorruptionError(
                        "temporal index locator ordinal is out of range"
                    )
                version = commit.versions[ordinal]
                if (
                    version.version_id != value
                    or version.commit_id != commit_id
                    or version.commit_ordinal != ordinal
                ):
                    raise TemporalCorruptionError(
                        "temporal index locator does not match its authenticated version"
                    )
                resolved[value] = self.copy_payload(version)
            if missing:
                # One bounded canonical pass proves absence or exposes an
                # omission despite claimed index coverage. A full scan is
                # required to prove nonexistence; hits are rare because a
                # usable index should have located every visible target.
                for commit_id in range(1, through_commit + 1):
                    commit = self.get_commit_cached(commit_id, _validate_supersession=False)
                    if commit is None:
                        continue
                    for version in commit.versions:
                        if version.version_id in missing:
                            # Index claimed coverage but omitted this record.
                            raise TemporalCorruptionError(
                                "temporal version index omitted authenticated membership"
                            )
                for value in missing:
                    resolved[value] = None
            return resolved
        # Deferred or absent indexes: one bounded canonical pass, no side effects.
        needed = set(distinct)
        for commit_id in range(1, through_commit + 1):
            if not needed:
                break
            commit = self.get_commit_cached(commit_id, _validate_supersession=False)
            if commit is None:
                continue
            for version in commit.versions:
                if version.version_id in needed and resolved[version.version_id] is None:
                    resolved[version.version_id] = self.copy_payload(version)
                    # Version IDs are unique; keep earliest and ignore later
                    # duplicates here to preserve historical lookup behavior.
                    # Index builds reject duplicates before they can persist.
                    needed.discard(version.version_id)
            if not needed:
                break
        return resolved

    def get_version_through(self, version_id: str, through_commit: int) -> TemporalVersion | None:
        """Return the latest version with an ID up to a commit horizon."""
        normalized = normalize_version_id(version_id)
        if isinstance(through_commit, bool) or not isinstance(through_commit, int):
            raise ValueError("through_commit must be a non-negative integer")
        if through_commit < 0:
            raise ValueError("through_commit must be a non-negative integer")
        return self.get_versions_through_batch([normalized], through_commit)[normalized]

    def system_horizon(self, *, system_time: PointInput | None = None, through_commit: int | None = None) -> int:
        """Resolve a system-time/through-commit selection to a commit horizon."""
        if system_time is not None and through_commit is not None:
            raise ValueError("provide either system_time or through_commit, not both")
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.sequence_key, self.store_path, self.sequence_filename)
        latest_visible = last_commit_id
        while latest_visible > 0 and self.get_commit(
            latest_visible, _validate_supersession=False
        ) is None:
            latest_visible -= 1
        if through_commit is not None:
            if isinstance(through_commit, bool) or not isinstance(through_commit, int) or through_commit < 0:
                raise ValueError("through_commit must be a non-negative integer")
            return min(latest_visible, through_commit)
        if system_time is None:
            return latest_visible
        requested = normalize_temporal_instant(system_time)
        _temporal_indexes.ensure_temporal_indexes(
            self.store, self.state_key, self.stale_key, latest_visible)
        for encoded_commit_id in self.store.iter_range_index(
            _temporal_indexes.index_name(
                _temporal_indexes.SYSTEM_INDEX,
                _temporal_indexes.index_state_generation(self.store, self.state_key),
            ),
            [b"commit"],
            None,
            temporal_instant_index_value(requested),
            True,
            True,
            reverse=True,
        ):
            try:
                commit_id = int(encoded_commit_id, 16)
            except (TypeError, ValueError) as exc:
                raise TemporalCorruptionError("invalid temporal system index value") from exc
            if commit_id <= latest_visible and self.get_commit(
                commit_id, _validate_supersession=False
            ) is not None:
                return commit_id
        return 0

    # -- typed version entry points ------------------------------------

    def _version_by_id(self, version_id: str, kind: type[VersionT], *, system_time: PointInput | None = None,
                       through_commit: int | None = None) -> VersionT | None:
        horizon = self.system_horizon(system_time=system_time, through_commit=through_commit)
        _temporal_indexes.ensure_temporal_indexes(
            self.store, self.state_key, self.stale_key, horizon)
        locators = list(self.store.iter_index_prefix(
            _temporal_indexes.index_name(
                _temporal_indexes.VERSION_INDEX,
                _temporal_indexes.index_state_generation(self.store, self.state_key),
            ),
            [normalize_version_id(version_id).encode("ascii")]
        ))
        version = self.get_version_at(locators[-1]) if locators else None
        if version is not None and version.commit_id > horizon:
            return None
        return version if isinstance(version, kind) else None

    def get_node_version(self, version_id: str, *, system_time: PointInput | None = None,
                         through_commit: int | None = None) -> NodeVersion | None:
        """Return a visible node version by UUID using the temporal index."""
        return self._version_by_id(version_id, NodeVersion, system_time=system_time,
                                   through_commit=through_commit)

    def get_edge_version(self, version_id: str, *, system_time: PointInput | None = None,
                         through_commit: int | None = None) -> EdgeVersion | None:
        """Return a visible edge version by UUID using the temporal index."""
        return self._version_by_id(version_id, EdgeVersion, system_time=system_time,
                                   through_commit=through_commit)

    def get_claim_version(self, version_id: str, *, system_time: PointInput | None = None,
                          through_commit: int | None = None) -> ClaimVersion | None:
        """Return a visible epistemic claim version by UUID."""
        return self._version_by_id(version_id, ClaimVersion, system_time=system_time,
                                   through_commit=through_commit)


    # -- point-in-time selection ------------------------------------------


def claim_valid_interval(valid: TemporalIntervalInput | None, valid_from: PointInput | None, valid_to: PointInput | None, *, required: bool) -> TemporalInterval | None:
    """Normalize claim validity inputs to at most one half-open interval."""
    if valid is not None and (valid_from is not None or valid_to is not None):
        raise TemporalVersionError("provide either valid or valid_from/valid_to, not both")
    if valid is not None:
        return normalize_temporal_interval(valid)
    if valid_from is None:
        if valid_to is not None:
            raise TemporalVersionError("valid_to requires valid_from")
        if required:
            raise TemporalVersionError("claim assertion requires valid or valid_from")
        return None
    return TemporalInterval(
        normalize_temporal_instant(valid_from),
        None if valid_to is None else normalize_temporal_instant(valid_to),
    )


def open_temporal_view(graph: TemporalReadSource, reader: "TemporalReader", *, manifest: Mapping[str, Any],
                       database_id: str, valid_time: PointInput, system_time: PointInput | None = None,
                       through_commit: int | None = None) -> Iterator[GraphReadView]:
    """Yield a temporal read view pinned to one visible commit horizon.

    ``graph`` is the live handle the returned view binds to; all history
    reads go through ``reader``. The caller owns transaction/snapshot
    scope and keeps the view's context open while consumers run.
    """
    from ..readview import GraphReadView, ReadViewProvenance

    instant = normalize_temporal_instant(valid_time)
    if system_time is not None and through_commit is not None:
        raise ValueError("provide either system_time or through_commit, not both")
    allocated, _ = read_temporal_sequence(
        reader.store, reader.sequence_key, reader.store_path,
        reader.sequence_filename)
    latest = 0
    markers = []
    for commit_id in range(1, allocated + 1):
        commit_candidate = reader.get_commit(commit_id, _validate_supersession=False)
        if commit_candidate is None:
            if reader.store.get_metadata(temporal_commit_key(commit_id)) is None:
                continue
            break
        marker_candidate = reader.store.get_metadata(temporal_visible_key(commit_id))
        markers.append(commit_id.to_bytes(8, "big") + cast(bytes, marker_candidate))
        latest = commit_id
    if system_time is not None:
        through_commit = min(
            latest, reader.system_horizon(system_time=system_time)
        )
    if through_commit is None:
        horizon = latest
    else:
        if isinstance(through_commit, bool) or not isinstance(through_commit, int) or through_commit < 0:
            raise ValueError("through_commit must be a non-negative integer")
        if through_commit > latest:
            raise ValueError("through_commit is newer than the latest visible commit")
        horizon = through_commit
    commit = reader.get_commit(horizon) if horizon else None
    if horizon and commit is None:
        raise ValueError("through_commit does not identify a visible commit")
    marker = reader.store.get_metadata(temporal_visible_key(horizon)) if horizon else None
    visibility_digest = hashlib.sha256(b"".join(
        value for value in markers if int.from_bytes(value[:8], "big") <= horizon
    )).hexdigest()
    provenance = ReadViewProvenance(
        database_id=database_id,
        commit_horizon=horizon,
        commit_system_time_us=None if commit is None else commit.system_time.epoch_microseconds,
        commit_marker_sha256=None if marker is None else marker.hex(),
        visibility_sha256=visibility_digest,
        valid_time_us=instant.epoch_microseconds,
        backend_name=manifest["backend"]["name"],
        backend_layout_version=manifest["backend"]["layout_version"],
        serializer_name=manifest["serializer"]["name"],
        serializer_format_version=manifest["serializer"]["format_version"],
    )
    yield GraphReadView(graph, provenance)
