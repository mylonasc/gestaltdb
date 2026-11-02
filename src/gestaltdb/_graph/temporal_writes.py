"""Temporal commit write path (Phase 5 extraction, writer side).

``TemporalCommitter`` validates a batch of version writes, reserves a commit
ID, publishes descriptors/records/indexes/visibility markers, and returns
the committed result. All historical reads go through an explicit
``TemporalReader`` bound to the same scope; the writer guard, transaction
dispatch, and deferred-rebuild orchestration stay on ``GraphDB``.

``commit_direct`` runs on exactly one store: the handle that owns the
committer. Transaction graphs build their own committer, so transactional
writes never touch the parent store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Mapping, Sequence, cast

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..epistemic import Claim
from ..versioning import (
    ClaimVersion,
    ClaimVersionWrite,
    EdgeVersion,
    EdgeVersionWrite,
    NodeVersion,
    NodeVersionWrite,
    TemporalCommit,
    TemporalCorruptionError,
    TemporalVersionError,
    VersionOperation,
    commit_descriptor_bytes,
    encode_version_envelope,
    canonical_json_bytes,
    normalize_logical_id,
    normalize_temporal_interval,
    normalize_version_id,
)
from ..ingestion import IndexMaintenanceMode
from . import index_maintenance as _index_maintenance
from . import temporal_indexes as _temporal_indexes
from .entities import Edge, Node
from .entities import GraphEntityDictSerializer
from .temporal_types import Clock, EntityKind, PreparedWrite, RetractionIdentity, VersionWrite
from .temporal_keys import temporal_commit_key, temporal_record_key, temporal_visible_key
from .temporal_reads import TemporalReader
from .temporal_sequence import read_temporal_sequence, reserve_temporal_sequence





def require_retraction_identities(reader: TemporalReader, identities: Iterable[RetractionIdentity]) -> None:
    """Validate retractions against published history, not deferred indexes.

    Scans once for all unresolved identities in the batch; unpublished
    records and prior retraction-only history cannot establish identity.
    """
    missing = set(identities)
    if not missing:
        return
    last_commit_id, _ = read_temporal_sequence(
        reader.store, reader.sequence_key, reader.store_path,
        reader.sequence_filename)
    for commit_id in range(1, last_commit_id + 1):
        commit = reader.get_commit(commit_id, _validate_supersession=False)
        if commit is None:
            continue
        for version in commit.versions:
            if version.operation is VersionOperation.RETRACT:
                continue
            kind: EntityKind = "node" if isinstance(version, NodeVersion) else (
                "edge" if isinstance(version, EdgeVersion) else "claim"
            )
            missing.discard((kind, version.logical_id))
        if not missing:
            return
    kind, logical_id = sorted(missing)[0]
    raise TemporalVersionError(
        f"cannot retract nonexistent {kind} identity: {logical_id}"
    )


@dataclass
class TemporalCommitter:
    """Store-bound temporal write capability for one graph scope.

    ``require_retraction_fn`` is the facade's retraction-identity hook
    (bound ``GraphDB._require_temporal_retraction_identities``): tests
    monkeypatch that hook to simulate legacy retraction-only history, so
    the commit path must route through it rather than calling the module
    implementation directly.
    """

    store: KVStore
    entity_serializer: GraphEntityDictSerializer
    reader: TemporalReader
    clock: Clock
    require_retraction_fn: Callable[[Iterable[RetractionIdentity]], None]

    # -- validation helpers ---------------------------------------------

    def version_id_exists(self, version_id: str) -> bool:
        """Return whether a version ID is already visible."""
        version_id = normalize_version_id(version_id)
        if _temporal_indexes.index_state_horizon(self.store, self.reader.state_key) is not None:
            # Indexes are current, so the version index answers in O(1).
            # It is written for every commit in MAINTAIN mode and covers
            # all visible history once the index state exists.
            for _ in self.store.iter_index_prefix(
                _temporal_indexes.index_name(
                    _temporal_indexes.VERSION_INDEX,
                    _temporal_indexes.index_state_generation(self.store, self.reader.state_key),
                ),
                [version_id.encode("ascii")],
            ):
                return True
            return False
        # Stale or absent indexes (e.g. DEFER bulk loads): fall back to a
        # descriptor scan. Slower, but correct in every index mode.
        last_commit_id, _ = read_temporal_sequence(
            self.store, self.reader.sequence_key, self.reader.store_path,
            self.reader.sequence_filename)
        for commit_id in range(1, last_commit_id + 1):
            descriptor_payload = self.store.get_metadata(temporal_commit_key(commit_id))
            if descriptor_payload is None:
                continue
            try:
                descriptor = json.loads(descriptor_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(descriptor, dict):
                raise TemporalCorruptionError("invalid temporal commit descriptor")
            if version_id in descriptor.get("version_ids", []):
                return True
        return False

    # -- commit ----------------------------------------------------------

    def commit_direct(self, writes: Sequence[VersionWrite], metadata: Mapping[str, object], index_mode: str) -> TemporalCommit:
        """Validate, sequence, and publish one commit on this scope's store."""
        store = self.store
        entity_serializer = self.entity_serializer
        # Batch supersession resolution: distinct targets once against the
        # captured pre-write horizon. Same-batch and future records cannot be
        # selected because the horizon predates sequence reservation.
        pre_horizon, _ = read_temporal_sequence(
            store, self.reader.sequence_key, self.reader.store_path,
            self.reader.sequence_filename)
        distinct_supersedes: list[str] = []
        seen_supersedes: set[str] = set()
        for candidate in writes:
            supersedes = getattr(candidate, "supersedes_version_id", None)
            if supersedes is None:
                continue
            try:
                normalized_supersedes = normalize_version_id(supersedes)
            except (TypeError, ValueError):
                # Deferred to per-write validation to preserve exact errors.
                continue
            if normalized_supersedes not in seen_supersedes:
                seen_supersedes.add(normalized_supersedes)
                distinct_supersedes.append(normalized_supersedes)
        supersession_targets: dict[str, object] = {}
        if distinct_supersedes and pre_horizon > 0:
            supersession_targets = self.reader.get_versions_through_batch(
                distinct_supersedes, pre_horizon
            )
        prepared_inputs: list[PreparedWrite] = []
        batch_version_ids = set()
        known_identities: set[RetractionIdentity] = set()
        retraction_identities: set[RetractionIdentity] = set()
        for ordinal, write in enumerate(writes):
            if ordinal >= 1 << 32:
                raise TemporalVersionError("temporal commit contains too many versions")
            if not isinstance(write, (NodeVersionWrite, EdgeVersionWrite, ClaimVersionWrite)):
                raise TypeError(
                    "writes must contain NodeVersionWrite, EdgeVersionWrite, or ClaimVersionWrite values"
                )
            if not isinstance(write.operation, VersionOperation):
                raise TemporalVersionError("temporal version operation is invalid")
            valid_input = write.valid
            if valid_input is not None:
                try:
                    valid_input = normalize_temporal_interval(valid_input)
                except (TypeError, ValueError) as exc:
                    raise TemporalVersionError("temporal version validity interval is invalid") from exc
            version_id = normalize_version_id(write.version_id)
            if version_id in batch_version_ids or self.version_id_exists(version_id):
                raise TemporalVersionError(f"temporal version ID already exists: {version_id}")
            batch_version_ids.add(version_id)
            logical_id = normalize_logical_id(write.logical_id)
            target = None
            supersedes_version_id = None
            if write.supersedes_version_id is not None:
                supersedes_version_id = normalize_version_id(write.supersedes_version_id)
                if supersedes_version_id in supersession_targets:
                    target = supersession_targets[supersedes_version_id]
                else:
                    # Invalid IDs skipped during pre-resolution surface here
                    # with identical errors; unindexed horizons fall back to a
                    # single bounded lookup without index side effects.
                    target = self.reader.get_version(supersedes_version_id)
                if target is None:
                    raise TemporalVersionError("superseded temporal version does not exist")
                expected_version_type = {
                    NodeVersionWrite: NodeVersion,
                    EdgeVersionWrite: EdgeVersion,
                    ClaimVersionWrite: ClaimVersion,
                }[type(write)]
                if not isinstance(target, expected_version_type) or target.logical_id != logical_id:
                    raise TemporalVersionError("superseded temporal version has a different kind or logical ID")
            if write.operation is VersionOperation.ASSERT and write.supersedes_version_id is not None:
                raise TemporalVersionError("assertions cannot supersede another version")
            if write.operation is not VersionOperation.RETRACT and write.reason is not None:
                raise TemporalVersionError("only retractions may include a reason")
            if write.reason is not None and (not isinstance(write.reason, str) or not write.reason):
                raise TemporalVersionError("retraction reason must be a non-empty string")
            if write.operation is VersionOperation.CORRECT and target is None:
                raise TemporalVersionError("corrections require a superseded version")
            valid = valid_input or (target.valid if target is not None else None)
            if valid is None:
                raise TemporalVersionError("a validity interval or superseded version is required")

            if isinstance(write, NodeVersionWrite):
                entity_kind: EntityKind = "node"
                entity: Node | Edge | Claim | None = write.node
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Node):
                    raise TemporalVersionError("node assertions and corrections require a Node payload")
                if entity is not None and cast(Node, entity).get_id != logical_id:
                    raise TemporalVersionError("node payload ID does not match logical ID")
                payload = b"" if entity is None else entity_serializer.serialize(cast(Node, entity), "Node")
                entity_type = "Node"
            elif isinstance(write, EdgeVersionWrite):
                entity_kind = "edge"
                entity = write.edge
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Edge):
                    raise TemporalVersionError("edge assertions and corrections require an Edge payload")
                if entity is not None and entity.get_id != logical_id:
                    raise TemporalVersionError("edge payload ID does not match logical ID")
                payload = b"" if entity is None else entity_serializer.serialize(cast(Edge, entity), "Edge")
                entity_type = "Edge"
            else:
                entity_kind = "claim"
                entity = write.claim
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Claim):
                    raise TemporalVersionError("claim assertions and corrections require a Claim payload")
                if entity is not None and entity.claim_id != logical_id:
                    raise TemporalVersionError("claim payload ID does not match logical ID")
                payload = b"" if entity is None else canonical_json_bytes(entity.to_dict())
                entity_type = "Claim"
            if write.operation is VersionOperation.RETRACT and entity is not None:
                raise TemporalVersionError("retractions cannot contain entity payloads")
            if not isinstance(payload, bytes):
                raise TemporalVersionError("the configured serializer must return bytes")
            if write.operation is not VersionOperation.RETRACT:
                if not payload:
                    raise TemporalVersionError("temporal entity payload cannot be empty")
                if entity_type == "Claim":
                    try:
                        decoded_claim = Claim.from_dict(json.loads(payload.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError, TemporalVersionError) as exc:
                        raise TemporalVersionError("cannot decode temporal claim payload") from exc
                    if decoded_claim.claim_id != logical_id:
                        raise TemporalVersionError("serialized temporal payload changed its logical ID")
                else:
                    try:
                        decoded_entity = entity_serializer.deserialize(payload, cast("Literal['Node', 'Edge']", entity_type))
                    except Exception as exc:
                        raise TemporalVersionError(
                            "the configured serializer cannot decode its temporal payload"
                        ) from exc
                    expected_type = Node if entity_type == "Node" else Edge
                    if not isinstance(decoded_entity, expected_type) or decoded_entity.get_id != logical_id:
                        raise TemporalVersionError("serialized temporal payload changed its logical ID")

            prepared_inputs.append((
                entity_kind,
                version_id,
                logical_id,
                valid,
                write.operation,
                supersedes_version_id,
                write.reason,
                payload,
            ))

            identity = (entity_kind, logical_id)
            if write.operation is not VersionOperation.RETRACT or (
                target is not None and target.operation is not VersionOperation.RETRACT
            ):
                known_identities.add(identity)
            elif identity not in known_identities:
                retraction_identities.add(identity)

        self.require_retraction_fn(retraction_identities)
        commit_id, system_time = reserve_temporal_sequence(
            store, self.reader.sequence_key, self.reader.store_path,
            self.reader.sequence_filename, self.clock,
        )
        previous_commit_id = commit_id - 1
        prepared = []
        for ordinal, (
            entity_kind,
            version_id,
            logical_id,
            valid,
            operation,
            supersedes_version_id,
            reason,
            payload,
        ) in enumerate(prepared_inputs):
            header = {
                "entity_kind": entity_kind,
                "version_id": version_id,
                "logical_id": logical_id,
                "valid_from_us": valid.start.epoch_microseconds,
                "valid_to_us": None if valid.end is None else valid.end.epoch_microseconds,
                "commit_id": commit_id,
                "commit_ordinal": ordinal,
                "system_time_us": system_time.epoch_microseconds,
                "operation": operation.value,
                "supersedes_version_id": supersedes_version_id,
                "reason": reason,
            }
            prepared.append((version_id, encode_version_envelope(header, payload)))

        sequence = canonical_json_bytes({
            "commit_id": commit_id,
            "system_time_us": system_time.epoch_microseconds,
        })
        record_digests = [hashlib.sha256(envelope).hexdigest() for _, envelope in prepared]
        descriptor = commit_descriptor_bytes(
            commit_id,
            system_time,
            metadata,
            [version_id for version_id, _ in prepared],
            record_digests,
        )

        if store.get_metadata(temporal_commit_key(commit_id)) is not None:
            raise TemporalCorruptionError("temporal sequence would overwrite an existing commit")
        if store.get_metadata(temporal_visible_key(commit_id)) is not None:
            raise TemporalCorruptionError("temporal sequence would overwrite an existing marker")
        for ordinal in range(len(prepared)):
            if store.get_metadata(temporal_record_key(commit_id, ordinal)) is not None:
                raise TemporalCorruptionError("temporal sequence would overwrite an existing record")

        # The sequence reserves the ID. The visibility marker is always written last.
        if self.reader.store_path is not None:
            store.put_metadata(self.reader.sequence_key, sequence)
        store.put_metadata(temporal_commit_key(commit_id), descriptor)
        for ordinal, (_, envelope) in enumerate(prepared):
            store.put_metadata(temporal_record_key(commit_id, ordinal), envelope)
        indexes_publishable = False
        if index_mode == IndexMaintenanceMode.DEFER.value:
            _index_maintenance.mark_indexes_stale(store, self.reader.stale_key, "temporal")
        else:
            prior_visible = False
            if previous_commit_id > 0 and _temporal_indexes.index_state_horizon(
                    store, self.reader.state_key) is None:
                prior_visible = any(
                    self.reader.get_commit(candidate, _validate_supersession=False) is not None
                    for candidate in range(1, commit_id)
                )
            if prior_visible:
                _index_maintenance.mark_indexes_stale(store, self.reader.stale_key, "temporal")
            indexes_publishable = not prior_visible
            indexed_versions = [self.reader.decode_version(envelope) for _, envelope in prepared]
            _temporal_indexes.write_temporal_indexes(store, indexed_versions, commit_id, generation=(
                _temporal_indexes.index_state_generation(store, self.reader.state_key)))
        marker = hashlib.sha256(descriptor).digest()
        if indexes_publishable:
            _temporal_indexes.persist_index_state(
                store,
                self.reader.state_key,
                commit_id,
                _temporal_indexes.index_state_generation(store, self.reader.state_key),
                pending_marker=(commit_id, marker),
            )
        store.put_metadata(temporal_visible_key(commit_id), marker)
        commit = self.reader.get_commit(commit_id)
        if commit is None:
            raise TemporalCorruptionError("new temporal commit was not visible after publication")
        return commit
