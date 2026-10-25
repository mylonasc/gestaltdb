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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..epistemic import Claim
from ..temporal import TemporalInstant, TemporalInterval
from ..versioning import (
    ClaimVersion,
    EdgeVersion,
    NodeVersion,
    TemporalCommit,
    TemporalCorruptionError,
    TemporalVersionError,
    VersionOperation,
    decode_version_envelope,
    immutable_metadata,
    normalize_logical_id,
    normalize_temporal_instant,
    normalize_temporal_interval,
    normalize_version_id,
)
from . import temporal_indexes as _temporal_indexes
from .temporal_keys import (
    decode_temporal_locator,
    temporal_commit_key,
    temporal_instant_index_value,
    temporal_record_key,
    temporal_visible_key,
)
from .temporal_sequence import read_temporal_sequence


@dataclass
class TemporalReader:
    """Store-bound historical read capability for one graph scope."""

    store: KVStore
    entity_serializer: object
    commit_cache: dict
    sequence_key: bytes
    store_path: Path | None
    sequence_filename: str
    state_key: bytes
    stale_key: bytes

    # -- version decoding ---------------------------------------------

    def decode_version(self, envelope: bytes):
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
            common = {
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
            entity = None
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
            return NodeVersion(**common, node=entity)
        if header["entity_kind"] == "edge":
            return EdgeVersion(**common, edge=entity)
        if header["entity_kind"] == "claim":
            return ClaimVersion(**common, claim=entity)
        raise TemporalCorruptionError("unknown temporal entity kind")

    @staticmethod
    def copy_payload(version):
        """Detach mutable entity payloads from a cached version."""
        if isinstance(version, NodeVersion) and version.node is not None:
            return replace(version, node=copy.deepcopy(version.node))
        if isinstance(version, EdgeVersion) and version.edge is not None:
            return replace(version, edge=copy.deepcopy(version.edge))
        # Claim and its recursively frozen provenance are immutable values.
        return version

    # -- commit reads -------------------------------------------------

    def get_commit(self, commit_id: int, *, _validate_supersession: bool = True):
        """Return a validated visible commit with detached mutable entities.

        Decode reuse is bounded and scoped to the exact store object. Pinned
        verifiable read snapshots can reuse validated records directly;
        live/unverifiable stores recheck descriptor, marker and envelopes.
        """
        commit = self.get_commit_cached(commit_id, _validate_supersession=_validate_supersession)
        if commit is None:
            return None
        return replace(commit, versions=tuple(self.copy_payload(v) for v in commit.versions))

    def get_commit_cached(self, commit_id: int, *,
                          _validate_supersession: bool = True):
        """Return a cached validated commit, rechecking store markers."""
        if isinstance(commit_id, bool) or not isinstance(commit_id, int) or commit_id < 1:
            raise ValueError("commit_id must be a positive integer")
        cache_key = (id(self.store), commit_id, bool(_validate_supersession))
        cached = self.commit_cache.get(cache_key)
        identity = getattr(self.store, "snapshot_identity", None)
        if cached is not None and identity is not None and identity.verifiable and identity.sequence is not None:
            return cached[3]
        descriptor_payload = self.store.get_metadata(temporal_commit_key(commit_id))
        marker = self.store.get_metadata(temporal_visible_key(commit_id))
        if marker is None:
            self.commit_cache.pop(cache_key, None)
            return None
        if descriptor_payload is None or marker != hashlib.sha256(descriptor_payload).digest():
            raise TemporalCorruptionError("temporal commit marker does not match its descriptor")
        if cached is not None and cached[1] == descriptor_payload:
            envelopes = tuple(self.store.get_metadata(temporal_record_key(commit_id, ordinal))
                              for ordinal in range(len(cached[2])))
            if envelopes != cached[2]:
                raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            # Supersession targets can change on mutable stores too. Only
            # reuse the non-supersession validation result in this path.
            if not _validate_supersession or not any(v.supersedes_version_id for v in cached[3].versions):
                return cached[3]
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
        versions = []
        envelopes = []
        for ordinal, (version_id, expected_digest) in enumerate(zip(version_ids, record_digests)):
            envelope = self.store.get_metadata(temporal_record_key(commit_id, ordinal))
            if envelope is None or hashlib.sha256(envelope).hexdigest() != expected_digest:
                raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            envelopes.append(envelope)
            version = self.decode_version(envelope)
            if version.version_id != version_id or version.commit_id != commit_id or version.commit_ordinal != ordinal:
                raise TemporalCorruptionError("temporal commit record does not match its descriptor")
            if version.system_time != system_time:
                raise TemporalCorruptionError("temporal version system time does not match its commit")
            versions.append(version)
        if _validate_supersession:
            for version in versions:
                if version.supersedes_version_id is None:
                    continue
                target = self.get_version_through(
                    version.supersedes_version_id, commit_id - 1
                )
                if target is None:
                    raise TemporalCorruptionError("temporal version supersedes a missing version")
                if type(target) is not type(version) or target.logical_id != version.logical_id:
                    raise TemporalCorruptionError("temporal supersession kind or logical ID mismatch")
        commit = TemporalCommit(commit_id, system_time, metadata, tuple(versions))
        if len(self.commit_cache) >= 256:
            # Drop the oldest half; commits are immutable so eviction only
            # costs a future re-decode, never correctness.
            for old_key in list(self.commit_cache)[:128]:
                del self.commit_cache[old_key]
        self.commit_cache[cache_key] = (self.store, descriptor_payload, tuple(envelopes), commit)
        return commit

    def iter_commits(self, *, through_commit=None):
        """Iterate visible commits in commit order, skipping incomplete commits."""
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.sequence_key, self.store_path, self.sequence_filename)
        limit = last_commit_id if through_commit is None else min(last_commit_id, through_commit)
        for commit_id in range(1, limit + 1):
            commit = self.get_commit(commit_id)
            if commit is not None:
                yield commit

    # -- version lookups ----------------------------------------------

    def get_version(self, version_id: str):
        """Return the latest visible version with an ID, scanning all commits."""
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.sequence_key, self.store_path, self.sequence_filename)
        return self.get_version_through(version_id, last_commit_id)

    def get_version_at(self, locator: bytes):
        """Return the version addressed by an opaque index locator."""
        commit_id, ordinal = decode_temporal_locator(locator)
        commit = self.get_commit_cached(commit_id, _validate_supersession=False)
        if commit is None:
            return None
        if ordinal >= len(commit.versions):
            raise TemporalCorruptionError("temporal index locator ordinal is out of range")
        return self.copy_payload(commit.versions[ordinal])

    def get_version_through(self, version_id: str, through_commit: int):
        """Return the latest version with an ID up to a commit horizon."""
        version_id = normalize_version_id(version_id)
        for commit_id in range(1, through_commit + 1):
            commit = self.get_commit_cached(commit_id, _validate_supersession=False)
            if commit is None:
                continue
            for version in commit.versions:
                if version.version_id == version_id:
                    return self.copy_payload(version)
        return None

    def system_horizon(self, *, system_time=None, through_commit=None) -> int:
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

    def _version_by_id(self, version_id: str, kind, *, system_time=None,
                       through_commit=None):
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

    def get_node_version(self, version_id: str, *, system_time=None,
                         through_commit=None):
        """Return a visible node version by UUID using the temporal index."""
        return self._version_by_id(version_id, NodeVersion, system_time=system_time,
                                   through_commit=through_commit)

    def get_edge_version(self, version_id: str, *, system_time=None,
                         through_commit=None):
        """Return a visible edge version by UUID using the temporal index."""
        return self._version_by_id(version_id, EdgeVersion, system_time=system_time,
                                   through_commit=through_commit)

    def get_claim_version(self, version_id: str, *, system_time=None,
                          through_commit=None):
        """Return a visible epistemic claim version by UUID."""
        return self._version_by_id(version_id, ClaimVersion, system_time=system_time,
                                   through_commit=through_commit)


    # -- point-in-time selection ------------------------------------------

    def interval_locators(self, index_name, parts, instant):
        """Return index locators started but not yet ended at an instant."""
        generation = _temporal_indexes.index_state_generation(self.store, self.state_key)
        encoded = temporal_instant_index_value(instant)
        started = set(self.store.iter_range_index(
            _temporal_indexes.index_name(index_name, generation),
            parts,
            None,
            encoded,
            True,
            True,
        ))
        if not started:
            return ()
        unexpired = set(self.store.iter_range_index(
            _temporal_indexes.index_name(_temporal_indexes.VALID_END_INDEX, generation),
            [index_name.encode("ascii"), *parts],
            encoded,
            None,
            False,
            True,
        ))
        return tuple(sorted(started.intersection(unexpired)))

    def entity_as_of(self, kind, logical_id, *, valid_time, system_time=None,
                     through_commit=None, hydrate=None):
        """Resolve one logical node/edge/claim version at a valid time.

        ``hydrate`` decodes one locator; callers pass the facade hook so
        instrumentation of version hydration keeps working.
        """
        hydrate = hydrate or self.get_version_at
        logical_id = normalize_logical_id(logical_id)
        instant = normalize_temporal_instant(valid_time)
        horizon = self.system_horizon(system_time=system_time, through_commit=through_commit)
        _temporal_indexes.ensure_temporal_indexes(
            self.store, self.state_key, self.stale_key, horizon)
        kind_bytes = {"node": b"n", "edge": b"e", "claim": b"c"}[kind]
        locators = self.interval_locators(
            _temporal_indexes.LOGICAL_VALID_INDEX,
            [kind_bytes, logical_id.encode("utf-8")],
            instant,
        )
        winner = None
        for locator in locators:
            version = hydrate(locator)
            if version is not None and version.commit_id <= horizon and version.valid.contains(instant):
                if winner is None or (version.commit_id, version.commit_ordinal) > (winner.commit_id, winner.commit_ordinal):
                    winner = version
        if winner is None or winner.operation is VersionOperation.RETRACT:
            return None
        return winner

    def iter_nodes_as_of(self, *, valid_time, system_time=None, through_commit=None,
                           hydrate=None):
        """Iterate node versions visible at one valid and system-time point."""
        hydrate = hydrate or self.get_version_at
        instant = normalize_temporal_instant(valid_time)
        horizon = self.system_horizon(system_time=system_time, through_commit=through_commit)
        _temporal_indexes.ensure_temporal_indexes(
            self.store, self.state_key, self.stale_key, horizon)
        logical_ids = set()
        for locator in self.interval_locators(
            _temporal_indexes.NODE_CATALOG_INDEX, [b"nodes"], instant
        ):
            candidate = hydrate(locator)
            if isinstance(candidate, NodeVersion) and candidate.commit_id <= horizon:
                logical_ids.add(candidate.logical_id)
        for logical_id in sorted(logical_ids):
            version = self.entity_as_of(
                "node", logical_id, valid_time=instant, through_commit=horizon,
                hydrate=hydrate,
            )
            if version is not None:
                yield version

    def iter_edges_as_of(self, *, edge_type=None, direction="out", source=None,
                         target=None, valid_time, system_time=None,
                         through_commit=None, hydrate=None):
        """Iterate endpoint edges visible at valid time and a system horizon."""
        hydrate = hydrate or self.get_version_at
        if direction not in {"out", "in"}:
            raise ValueError("direction must be 'out' or 'in'")
        if direction == "out" and (source is None or target is not None):
            raise ValueError("outgoing traversal requires source and rejects target")
        if direction == "in" and (target is None or source is not None):
            raise ValueError("incoming traversal requires target and rejects source")
        if edge_type is not None and (not isinstance(edge_type, str) or not edge_type):
            raise ValueError("edge_type must be a non-empty string or None")
        horizon = self.system_horizon(system_time=system_time, through_commit=through_commit)
        _temporal_indexes.ensure_temporal_indexes(
            self.store, self.state_key, self.stale_key, horizon)
        node_id = source if direction == "out" else target
        if edge_type is None:
            index_name = (
                _temporal_indexes.EDGE_OUT_CATALOG_INDEX
                if direction == "out"
                else _temporal_indexes.EDGE_IN_CATALOG_INDEX
            )
            parts = [str(node_id).encode("utf-8")]
        else:
            index_name = _temporal_indexes.EDGE_OUT_INDEX if direction == "out" else _temporal_indexes.EDGE_IN_INDEX
            parts = [str(node_id).encode("utf-8"), edge_type.encode("utf-8")]
        instant = normalize_temporal_instant(valid_time)
        locators = self.interval_locators(index_name, parts, instant)
        logical_ids = set()
        for locator in locators:
            candidate = hydrate(locator)
            if candidate is not None and candidate.commit_id <= horizon:
                logical_ids.add(candidate.logical_id)
        for logical_id in sorted(logical_ids):
            version = self.entity_as_of(
                "edge", logical_id, valid_time=valid_time, through_commit=horizon,
                hydrate=hydrate,
            )
            if version is None or (
                edge_type is not None and version.edge.properties.get("type") != edge_type
            ):
                continue
            if direction == "out" and str(version.edge.source) != str(source):
                continue
            if direction == "in" and str(version.edge.target) != str(target):
                continue
            yield version


def claim_valid_interval(valid, valid_from, valid_to, *, required):
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

