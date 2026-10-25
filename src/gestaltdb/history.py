"""Read-only temporal capabilities, independent of the mutable graph API.

Use ``graph.history`` for a scope-bound capability. It does not own or close
the store, open snapshots, or grant mutation/publication rights. Keep the
owning graph/transaction/snapshot open for the lifetime of its consumers.
See the runnable capability example in ``docs/architecture.rst``.
"""

from __future__ import annotations

from pathlib import Path

from ._graph import temporal_selection as selection
from ._graph.entities import GraphEntityDictSerializer
from ._graph.temporal_indexes import INDEX_STATE_KEY
from ._graph.temporal_keys import _TEMPORAL_SEQUENCE_FILENAME, _TEMPORAL_SEQUENCE_KEY
from ._graph.temporal_reads import TemporalReader


class TemporalHistory:
    """Historical reads without node writes, Cypher, sampling, or rule execution.

    ``reader`` supplies storage, codec, cache and temporal identity configuration;
    no GraphDB instance is required. Optional hooks preserve instrumentation and
    detect rebinding of a mutable owner during grouped claim reads.
    """

    def __init__(self, reader: TemporalReader, *, hydrate=None, live_store=None):
        self._reader = reader
        self._hydrate = hydrate
        self._live_store = live_store

    @classmethod
    def from_store(cls, store, serializer, *, store_path=None):
        """Borrow an existing store for historical reads without a GraphDB.

        Supply the database directory as ``store_path`` for handles whose path
        is not exposed (for example a backend snapshot). This performs no
        initialization, index repair, or writes. It does not authenticate a
        source or freeze a mutable store; use an authenticated read view when
        those guarantees are required.
        """
        path = getattr(store, "path", None) if store_path is None else store_path
        return cls(TemporalReader(
            store=store, entity_serializer=GraphEntityDictSerializer(serializer),
            commit_cache={}, sequence_key=_TEMPORAL_SEQUENCE_KEY,
            store_path=None if path is None else Path(path),
            sequence_filename=_TEMPORAL_SEQUENCE_FILENAME,
            state_key=INDEX_STATE_KEY, stale_key=b"schema:indexes:stale",
        ))

    def get_temporal_commit(self, commit_id, *, _validate_supersession=True):
        """Read a validated commit with detached mutable payloads."""
        return self._reader.get_commit(
            commit_id, _validate_supersession=_validate_supersession)

    def iter_temporal_commits(self, *, through_commit=None):
        """Iterate visible commits through an optional allocation horizon."""
        return self._reader.iter_commits(through_commit=through_commit)

    def get_node_version(self, version_id, **horizon):
        """Read a node version by UUID at an optional system horizon."""
        return self._reader.get_node_version(version_id, **horizon)

    def get_edge_version(self, version_id, **horizon):
        """Read an edge version by UUID at an optional system horizon."""
        return self._reader.get_edge_version(version_id, **horizon)

    def get_claim_version(self, version_id, **horizon):
        """Read a claim version by UUID at an optional system horizon."""
        return self._reader.get_claim_version(version_id, **horizon)

    def iter_node_versions(self, logical_id=None, **horizon):
        """Iterate all or one node's version history."""
        return selection.iter_node_versions(self._reader, logical_id, **horizon)

    def iter_edge_versions(self, logical_id=None, **horizon):
        """Iterate all or one edge's version history."""
        return selection.iter_edge_versions(self._reader, logical_id, **horizon)

    def iter_claim_versions(self, claim_id=None, **horizon):
        """Iterate all or one claim's version history."""
        return selection.iter_claim_versions(self._reader, claim_id, **horizon)

    def get_node_as_of(self, logical_id, **point):
        """Resolve a node at a valid-time point and optional system horizon."""
        return selection.entity_as_of(
            self._reader, "node", logical_id, hydrate=self._hydrate, **point)

    def entity_as_of(self, kind, logical_id, **point):
        """Resolve one logical node/edge/claim version at a valid time."""
        return selection.entity_as_of(
            self._reader, kind, logical_id, hydrate=self._hydrate, **point)

    def get_edge_as_of(self, logical_id, **point):
        """Resolve an edge at a valid-time point and optional system horizon."""
        return selection.entity_as_of(
            self._reader, "edge", logical_id, hydrate=self._hydrate, **point)

    def get_claim_as_of(self, claim_id, **point):
        """Resolve a claim at a valid-time point and optional system horizon."""
        return selection.entity_as_of(
            self._reader, "claim", claim_id, hydrate=self._hydrate, **point)

    def iter_nodes_as_of(self, **point):
        """Iterate nodes visible at a temporal point."""
        return selection.iter_nodes_as_of(
            self._reader, hydrate=self._hydrate, **point)

    def iter_edges_as_of(self, **point):
        """Iterate endpoint edges visible at a temporal point."""
        return selection.iter_edges_as_of(
            self._reader, hydrate=self._hydrate, **point)

    def iter_claims_as_of(self, **filters):
        """Iterate indexed claims with grouped commit-integrity admission."""
        return selection.iter_claims_as_of(
            self._reader, live_store=self._live_store, **filters)

    def claim_status(self, subject, predicate, object, **filters):
        """Return the four-valued status of a proposition."""
        return selection.claim_status(
            self._reader, subject, predicate, object, **filters)
