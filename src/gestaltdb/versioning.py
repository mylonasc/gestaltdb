"""Immutable temporal version records for GestaltDB.

The models in this module describe append-only history. Current-state graph
records, temporal indexes, and as-of traversal are separate concerns.
"""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterable, Mapping, Optional, Tuple, Union

from .temporal import TemporalInput, TemporalInstant, TemporalInterval, as_temporal_interval

if TYPE_CHECKING:
    from .epistemic import Claim
    from .graphdb import Edge, Node


_ENVELOPE_MAGIC = b"GTV1"
_HEADER_LENGTH = struct.Struct(">I")


class TemporalVersionError(ValueError):
    """Base error for invalid temporal version operations."""


class TemporalCorruptionError(TemporalVersionError):
    """Raised when marker-backed temporal history fails integrity checks."""


class VersionOperation(str, Enum):
    """Operation represented by an immutable entity version."""

    ASSERT = "assert"
    CORRECT = "correct"
    RETRACT = "retract"


def canonical_json_bytes(value: object) -> bytes:
    """Encode JSON deterministically, rejecting NaN and unsupported values."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TemporalVersionError("temporal metadata must be JSON-compatible") from exc


def normalize_version_id(value: Optional[str] = None) -> str:
    """Generate or validate a canonical lowercase UUID version ID."""
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str):
        raise TypeError("version_id must be a string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise TemporalVersionError("version_id must be a canonical UUID string") from exc
    normalized = str(parsed)
    if value != normalized:
        raise TemporalVersionError("version_id must be a canonical lowercase UUID string")
    return normalized


def normalize_logical_id(value: object) -> str:
    """Validate a stable logical entity ID."""
    if not isinstance(value, str) or not value:
        raise TemporalVersionError("logical entity ID must be a non-empty string")
    value.encode("utf-8")
    return value


def normalize_temporal_interval(
    value: Union[TemporalInterval, Tuple[TemporalInput, Optional[TemporalInput]]]
) -> TemporalInterval:
    return as_temporal_interval(value)


def normalize_temporal_instant(value: Union[TemporalInput, str]) -> TemporalInstant:
    if isinstance(value, str):
        return TemporalInstant.parse(value)
    return TemporalInstant.from_value(value)


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def immutable_metadata(value: Optional[Mapping[str, object]]) -> Mapping[str, object]:
    copied = json.loads(canonical_json_bytes(dict(value or {})).decode("utf-8"))
    return _freeze_json(copied)  # type: ignore[return-value]


@dataclass(frozen=True)
class TemporalVersionMetadata:
    """Metadata common to node and edge versions."""

    version_id: str
    logical_id: str
    valid: TemporalInterval
    commit_id: int
    commit_ordinal: int
    system_time: TemporalInstant
    operation: VersionOperation
    payload_hash: str
    supersedes_version_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class NodeVersion(TemporalVersionMetadata):
    """An immutable metadata envelope with an optional node payload."""

    node: Optional["Node"] = None


@dataclass(frozen=True)
class EdgeVersion(TemporalVersionMetadata):
    """An immutable metadata envelope with an optional edge payload."""

    edge: Optional["Edge"] = None


@dataclass(frozen=True)
class ClaimVersion(TemporalVersionMetadata):
    """An immutable metadata envelope with an optional epistemic claim."""

    claim: Optional["Claim"] = None


TemporalVersion = Union[NodeVersion, EdgeVersion, ClaimVersion]


@dataclass(frozen=True)
class TemporalCommit:
    """One visible temporal commit and its ordered entity versions."""

    commit_id: int
    system_time: TemporalInstant
    metadata: Mapping[str, object]
    versions: Tuple[TemporalVersion, ...]


@dataclass(frozen=True)
class NodeVersionWrite:
    """Validated input describing a node-version operation."""

    operation: VersionOperation
    logical_id: str
    valid: Optional[TemporalInterval]
    node: Optional["Node"] = None
    version_id: Optional[str] = None
    supersedes_version_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def assertion(cls, node: "Node", valid, *, version_id=None) -> "NodeVersionWrite":
        return cls(
            VersionOperation.ASSERT,
            normalize_logical_id(node.get_id),
            normalize_temporal_interval(valid),
            node=node,
            version_id=version_id,
        )

    @classmethod
    def correction(
        cls, node: "Node", *, supersedes_version_id: str, valid=None, version_id=None
    ) -> "NodeVersionWrite":
        return cls(
            VersionOperation.CORRECT,
            normalize_logical_id(node.get_id),
            None if valid is None else normalize_temporal_interval(valid),
            node=node,
            version_id=version_id,
            supersedes_version_id=normalize_version_id(supersedes_version_id),
        )

    @classmethod
    def retraction(
        cls,
        logical_id: str,
        *,
        valid=None,
        valid_from=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
    ) -> "NodeVersionWrite":
        return cls(
            VersionOperation.RETRACT,
            normalize_logical_id(logical_id),
            _retraction_interval(valid, valid_from),
            version_id=version_id,
            supersedes_version_id=(
                None if supersedes_version_id is None else normalize_version_id(supersedes_version_id)
            ),
            reason=_normalize_reason(reason),
        )


@dataclass(frozen=True)
class EdgeVersionWrite:
    """Validated input describing an edge-version operation."""

    operation: VersionOperation
    logical_id: str
    valid: Optional[TemporalInterval]
    edge: Optional["Edge"] = None
    version_id: Optional[str] = None
    supersedes_version_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def assertion(cls, edge: "Edge", valid, *, version_id=None) -> "EdgeVersionWrite":
        return cls(
            VersionOperation.ASSERT,
            normalize_logical_id(edge.get_id),
            normalize_temporal_interval(valid),
            edge=edge,
            version_id=version_id,
        )

    @classmethod
    def correction(
        cls, edge: "Edge", *, supersedes_version_id: str, valid=None, version_id=None
    ) -> "EdgeVersionWrite":
        return cls(
            VersionOperation.CORRECT,
            normalize_logical_id(edge.get_id),
            None if valid is None else normalize_temporal_interval(valid),
            edge=edge,
            version_id=version_id,
            supersedes_version_id=normalize_version_id(supersedes_version_id),
        )

    @classmethod
    def retraction(
        cls,
        logical_id: str,
        *,
        valid=None,
        valid_from=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
    ) -> "EdgeVersionWrite":
        return cls(
            VersionOperation.RETRACT,
            normalize_logical_id(logical_id),
            _retraction_interval(valid, valid_from),
            version_id=version_id,
            supersedes_version_id=(
                None if supersedes_version_id is None else normalize_version_id(supersedes_version_id)
            ),
            reason=_normalize_reason(reason),
        )


@dataclass(frozen=True)
class ClaimVersionWrite:
    """Validated input describing an epistemic claim-version operation."""

    operation: VersionOperation
    logical_id: str
    valid: Optional[TemporalInterval]
    claim: Optional["Claim"] = None
    version_id: Optional[str] = None
    supersedes_version_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def assertion(cls, claim: "Claim", valid, *, version_id=None) -> "ClaimVersionWrite":
        return cls(
            VersionOperation.ASSERT,
            normalize_logical_id(claim.claim_id),
            normalize_temporal_interval(valid),
            claim=claim,
            version_id=version_id,
        )

    @classmethod
    def correction(
        cls, claim: "Claim", *, supersedes_version_id: str, valid=None, version_id=None
    ) -> "ClaimVersionWrite":
        return cls(
            VersionOperation.CORRECT,
            normalize_logical_id(claim.claim_id),
            None if valid is None else normalize_temporal_interval(valid),
            claim=claim,
            version_id=version_id,
            supersedes_version_id=normalize_version_id(supersedes_version_id),
        )

    @classmethod
    def retraction(
        cls,
        claim_id: str,
        *,
        valid=None,
        valid_from=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
    ) -> "ClaimVersionWrite":
        return cls(
            VersionOperation.RETRACT,
            normalize_logical_id(claim_id),
            _retraction_interval(valid, valid_from),
            version_id=version_id,
            supersedes_version_id=(
                None if supersedes_version_id is None else normalize_version_id(supersedes_version_id)
            ),
            reason=_normalize_reason(reason),
        )


TemporalVersionWrite = Union[NodeVersionWrite, EdgeVersionWrite, ClaimVersionWrite]


def _retraction_interval(valid, valid_from) -> Optional[TemporalInterval]:
    if valid is not None and valid_from is not None:
        raise TemporalVersionError("provide either valid or valid_from, not both")
    if valid is not None:
        return normalize_temporal_interval(valid)
    if valid_from is not None:
        return TemporalInterval(normalize_temporal_instant(valid_from))
    return None


def _normalize_reason(reason: object) -> Optional[str]:
    if reason is None:
        return None
    if not isinstance(reason, str) or not reason:
        raise TemporalVersionError("retraction reason must be a non-empty string")
    return reason


def encode_version_envelope(header: Mapping[str, object], payload: bytes) -> bytes:
    """Encode a canonical temporal envelope around serializer-owned payload bytes."""
    if not isinstance(payload, bytes):
        raise TypeError("temporal version payload must be bytes")
    normalized = dict(header)
    normalized["format_version"] = 1
    normalized["payload_length"] = len(payload)
    normalized["payload_hash"] = hashlib.sha256(payload).hexdigest()
    header_bytes = canonical_json_bytes(normalized)
    return _ENVELOPE_MAGIC + _HEADER_LENGTH.pack(len(header_bytes)) + header_bytes + payload


def decode_version_envelope(value: bytes) -> Tuple[dict, bytes]:
    """Decode and validate a temporal version envelope."""
    if not isinstance(value, bytes) or len(value) < len(_ENVELOPE_MAGIC) + _HEADER_LENGTH.size:
        raise TemporalCorruptionError("invalid temporal version envelope")
    if not value.startswith(_ENVELOPE_MAGIC):
        raise TemporalCorruptionError("invalid temporal version envelope magic")
    offset = len(_ENVELOPE_MAGIC)
    (header_length,) = _HEADER_LENGTH.unpack(value[offset:offset + _HEADER_LENGTH.size])
    header_start = offset + _HEADER_LENGTH.size
    header_end = header_start + header_length
    if header_end > len(value):
        raise TemporalCorruptionError("truncated temporal version header")
    try:
        header = json.loads(value[header_start:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalCorruptionError("invalid temporal version header") from exc
    if not isinstance(header, dict):
        raise TemporalCorruptionError("temporal version header must be an object")
    payload = value[header_end:]
    if header.get("format_version") != 1:
        raise TemporalCorruptionError("unsupported temporal version format")
    if header.get("payload_length") != len(payload):
        raise TemporalCorruptionError("temporal version payload length mismatch")
    if header.get("payload_hash") != hashlib.sha256(payload).hexdigest():
        raise TemporalCorruptionError("temporal version payload hash mismatch")
    return header, payload


def commit_descriptor_bytes(
    commit_id: int,
    system_time: TemporalInstant,
    metadata: Mapping[str, object],
    version_ids: Iterable[str],
    record_digests: Iterable[str],
) -> bytes:
    return canonical_json_bytes({
        "format_version": 1,
        "commit_id": commit_id,
        "system_time_us": system_time.epoch_microseconds,
        "metadata": dict(metadata),
        "version_ids": list(version_ids),
        "record_digests": list(record_digests),
    })


__all__ = [
    "ClaimVersion",
    "ClaimVersionWrite",
    "EdgeVersion",
    "EdgeVersionWrite",
    "NodeVersion",
    "NodeVersionWrite",
    "TemporalCommit",
    "TemporalCorruptionError",
    "TemporalVersion",
    "TemporalVersionError",
    "TemporalVersionMetadata",
    "TemporalVersionWrite",
    "VersionOperation",
]
