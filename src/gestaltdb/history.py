"""Read-only temporal capabilities, independent of the mutable graph API.

Use ``graph.temporal.history()`` for a scope-bound capability. It does not own or close
the store, open snapshots, or grant mutation/publication rights. Keep the
owning graph/transaction/snapshot open for the lifetime of its consumers.
See the runnable capability example in ``docs/architecture.rst``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from ._graph import temporal_selection as selection
from ._graph.entities import GraphEntityDictSerializer
from ._graph.temporal_indexes import INDEX_STATE_KEY
from ._graph.temporal_keys import _TEMPORAL_SEQUENCE_FILENAME, _TEMPORAL_SEQUENCE_KEY
from ._graph.temporal_reads import TemporalReader
from ._graph.temporal_types import Hydrator, LiveStore, PointInput
from .epistemic import ClaimStatus
from .versioning import ClaimVersion, EdgeVersion, NodeVersion, TemporalCommit

if TYPE_CHECKING:
    from .kvstores import KVStore
    from .serializers import Serializer


class TemporalHistory:
    """Historical reads without node writes, Cypher, sampling, or rule execution.

    ``reader`` supplies storage, codec, cache and temporal identity configuration;
    no GraphDB instance is required. Optional hooks preserve instrumentation and
    detect rebinding of a mutable owner during grouped claim reads.
    """

    def __init__(self, reader: TemporalReader, *, hydrate: Hydrator | None = None, live_store: LiveStore | None = None) -> None:
        self._reader = reader
        self._hydrate = hydrate
        self._live_store = live_store

    @classmethod
    def from_store(cls, store: KVStore, serializer: Serializer, *, store_path: str | Path | None = None) -> TemporalHistory:
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

    def get_temporal_commit(self, commit_id: int, *, _validate_supersession: bool = True) -> TemporalCommit | None:
        """Read a validated commit with detached mutable payloads."""
        return self._reader.get_commit(
            commit_id, _validate_supersession=_validate_supersession)

    def iter_temporal_commits(self, *, through_commit: int | None = None) -> Iterator[TemporalCommit]:
        """Iterate visible commits through an optional allocation horizon."""
        return self._reader.iter_commits(through_commit=through_commit)

    def get_node_version(self, version_id: str, **horizon: Any) -> NodeVersion | None:
        """Read a node version by UUID at an optional system horizon."""
        return self._reader.get_node_version(version_id, **horizon)

    def get_edge_version(self, version_id: str, **horizon: Any) -> EdgeVersion | None:
        """Read an edge version by UUID at an optional system horizon."""
        return self._reader.get_edge_version(version_id, **horizon)

    def get_claim_version(self, version_id: str, **horizon: Any) -> ClaimVersion | None:
        """Read a claim version by UUID at an optional system horizon."""
        return self._reader.get_claim_version(version_id, **horizon)

    def iter_node_versions(self, logical_id: str | None = None, **horizon: Any) -> Iterator[NodeVersion]:
        """Iterate all or one node's version history."""
        return selection.iter_node_versions(self._reader, logical_id, **horizon)

    def iter_edge_versions(self, logical_id: str | None = None, **horizon: Any) -> Iterator[EdgeVersion]:
        """Iterate all or one edge's version history."""
        return selection.iter_edge_versions(self._reader, logical_id, **horizon)

    def iter_claim_versions(self, claim_id: str | None = None, **horizon: Any) -> Iterator[ClaimVersion]:
        """Iterate all or one claim's version history."""
        return selection.iter_claim_versions(self._reader, claim_id, **horizon)

    def get_node_as_of(self, logical_id: str, **point: Any) -> NodeVersion | None:
        """Resolve a node at a valid-time point and optional system horizon."""
        return cast(NodeVersion | None, selection.entity_as_of(
            self._reader, "node", logical_id, hydrate=self._hydrate, **point))

    @overload
    def entity_as_of(self, kind: Literal["node"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None) -> NodeVersion | None: ...
    @overload
    def entity_as_of(self, kind: Literal["edge"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None) -> EdgeVersion | None: ...
    @overload
    def entity_as_of(self, kind: Literal["claim"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None) -> ClaimVersion | None: ...
    @overload
    def entity_as_of(self, kind: str, logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None) -> NodeVersion | EdgeVersion | ClaimVersion | None: ...
    def entity_as_of(self, kind: str, logical_id: str, **point: Any) -> NodeVersion | EdgeVersion | ClaimVersion | None:
        """Resolve one logical node/edge/claim version at a valid time."""
        return selection.entity_as_of(
            self._reader, kind, logical_id, hydrate=self._hydrate, **point)

    def get_edge_as_of(self, logical_id: str, **point: Any) -> EdgeVersion | None:
        """Resolve an edge at a valid-time point and optional system horizon."""
        return cast(EdgeVersion | None, selection.entity_as_of(
            self._reader, "edge", logical_id, hydrate=self._hydrate, **point))

    def get_claim_as_of(self, claim_id: str, **point: Any) -> ClaimVersion | None:
        """Resolve a claim at a valid-time point and optional system horizon."""
        return cast(ClaimVersion | None, selection.entity_as_of(
            self._reader, "claim", claim_id, hydrate=self._hydrate, **point))

    def iter_nodes_as_of(self, **point: Any) -> Iterator[NodeVersion]:
        """Iterate nodes visible at a temporal point."""
        return selection.iter_nodes_as_of(
            self._reader, hydrate=self._hydrate, **point)

    def iter_edges_as_of(self, **point: Any) -> Iterator[EdgeVersion]:
        """Iterate endpoint edges visible at a temporal point."""
        return selection.iter_edges_as_of(
            self._reader, hydrate=self._hydrate, **point)

    def iter_claims_as_of(self, **filters: Any) -> Iterator[ClaimVersion]:
        """Iterate indexed claims with grouped commit-integrity admission."""
        return selection.iter_claims_as_of(
            self._reader, live_store=self._live_store, **filters)

    def claim_status(self, subject: str, predicate: str, object: object, **filters: Any) -> ClaimStatus:
        """Return the four-valued status of a proposition."""
        return selection.claim_status(
            self._reader, subject, predicate, object, **filters)
