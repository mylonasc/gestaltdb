"""Internal temporal contracts; raw JSON remains untrusted until validated.

These aliases describe existing cache/record shapes, not new storage formats.
The cache retains the exact store object as well as its identity-based key.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Mapping, Protocol, Sequence, TypeAlias, TypedDict, TypeVar

from ..temporal import TemporalInput, TemporalInstant, TemporalInterval
from ..ingestion import IndexMaintenanceMode
from ..versioning import (
    ClaimVersionWrite, EdgeVersionWrite, NodeVersionWrite, TemporalCommit,
    TemporalVersion, VersionOperation,
)

if TYPE_CHECKING:
    from .entities import GraphEntityDictSerializer
    from ..kvstores import KVStore
    from ..temporal_engine import TemporalEngine


PointInput: TypeAlias = TemporalInput | str
EntityKind: TypeAlias = Literal["node", "edge", "claim"]
VersionT = TypeVar("VersionT", bound=TemporalVersion)
VersionWrite: TypeAlias = NodeVersionWrite | EdgeVersionWrite | ClaimVersionWrite
Hydrator: TypeAlias = Callable[[bytes], TemporalVersion | None]
LiveStore: TypeAlias = Callable[[], "KVStore"]
Clock: TypeAlias = Callable[[], datetime | TemporalInstant]
CommitCache: TypeAlias = "dict[tuple[int, int, bool], tuple[KVStore, bytes, tuple[int, ...], TemporalCommit, GraphEntityDictSerializer, tuple[bytes, ...]]]"
RetractionIdentity: TypeAlias = tuple[EntityKind, str]
ExactEntry: TypeAlias = tuple[str, list[bytes], bytes]
RangeEntry: TypeAlias = tuple[str, list[bytes], bytes, bytes]
PreparedWrite: TypeAlias = tuple[EntityKind, str, str, TemporalInterval, VersionOperation, str | None, str | None, bytes]


class VersionFields(TypedDict):
    version_id: str
    logical_id: str
    valid: TemporalInterval
    commit_id: int
    commit_ordinal: int
    system_time: TemporalInstant
    operation: VersionOperation
    payload_hash: str
    supersedes_version_id: str | None
    reason: str | None


class IndexState(TypedDict):
    format_version: int | float  # The existing equality check also accepts JSON 5.0.
    active_generation: int
    indexed_through_commit: int
    visibility_checksum: str


class IndexWriter(Protocol):
    def __call__(self, versions: Sequence[TemporalVersion], commit_id: int, *, generation: int) -> dict[str, int]: ...


class CommitVersions(Protocol):
    def __call__(self, writes: Iterable[VersionWrite], *, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> TemporalCommit: ...


class RecordCatalog(TypedDict):
    """Validated catalog keys; other descriptor values remain untrusted."""

    version_ids: list[str]
    record_digests: list[str]


class TemporalReadSource(Protocol):
    """Read-view binding accepted from graph facades and Cypher read scopes."""

    @property
    def temporal(self) -> TemporalEngine: ...
    def key_to_string(self, key: str | bytes) -> str: ...
    def node_key_to_bytes(self, node_key: str | bytes) -> bytes: ...
