"""Immutable epistemic claim values and open-world status semantics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .versioning import TemporalVersionError, canonical_json_bytes


class ClaimPolarity(str, Enum):
    """Whether an agent asserts or denies a proposition."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


class ClaimObjectKind(str, Enum):
    """How a claim object is interpreted."""

    ENTITY = "entity"
    LITERAL = "literal"


class ClaimStatus(str, Enum):
    """Open-world four-valued status of a proposition at a read view."""

    SUPPORTED = "supported"
    REFUTED = "refuted"
    BOTH = "both"
    UNKNOWN = "unknown"


def _nonempty_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TemporalVersionError(f"claim {name} must be a non-empty string")
    value.encode("utf-8")
    return value


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _normalized_json(value: object, name: str) -> object:
    try:
        encoded = canonical_json_bytes(value)
        decoded = json.loads(encoded.decode("utf-8"))
    except TemporalVersionError as exc:
        raise TemporalVersionError(f"claim {name} must be JSON-compatible") from exc
    return _freeze_json(decoded)


def _identity(prefix: str, value: object) -> str:
    return f"{prefix}:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def claim_statement_id(
    subject: str,
    predicate: str,
    object: object,
    *,
    object_kind: ClaimObjectKind | str = ClaimObjectKind.ENTITY,
) -> str:
    """Return the deterministic identity of a subject-predicate-object proposition."""
    subject = _nonempty_text(subject, "subject")
    predicate = _nonempty_text(predicate, "predicate")
    try:
        kind = ClaimObjectKind(object_kind)
    except (TypeError, ValueError) as exc:
        raise TemporalVersionError("claim object_kind must be 'entity' or 'literal'") from exc
    claim_object = (
        _nonempty_text(object, "entity object")
        if kind is ClaimObjectKind.ENTITY
        else _normalized_json(object, "literal object")
    )
    return _identity("statement", {
        "subject": subject,
        "predicate": predicate,
        "object_kind": kind.value,
        "object": _thaw_json(claim_object),
    })


@dataclass(frozen=True)
class Claim:
    """A sourced assertion or denial, independent of temporal version metadata.

    ``statement_id`` identifies only the subject-predicate-object proposition.
    ``claim_id`` additionally identifies the asserting context (agent, source,
    world, and polarity), allowing contradictory claims to coexist.
    """

    subject: str
    predicate: str
    object: object
    polarity: ClaimPolarity | str
    agent: str
    source: str | None = None
    confidence: float | None = None
    world: str = "default"
    provenance: Mapping[str, object] = field(default_factory=dict)
    object_kind: ClaimObjectKind | str = ClaimObjectKind.ENTITY
    statement_id: str = field(init=False)
    claim_id: str = field(init=False)

    def __post_init__(self) -> None:
        subject = _nonempty_text(self.subject, "subject")
        predicate = _nonempty_text(self.predicate, "predicate")
        agent = _nonempty_text(self.agent, "agent")
        world = _nonempty_text(self.world, "world")
        source = None if self.source is None else _nonempty_text(self.source, "source")
        try:
            polarity = ClaimPolarity(self.polarity)
        except (TypeError, ValueError) as exc:
            raise TemporalVersionError("claim polarity must be 'positive' or 'negative'") from exc
        try:
            object_kind = ClaimObjectKind(self.object_kind)
        except (TypeError, ValueError) as exc:
            raise TemporalVersionError("claim object_kind must be 'entity' or 'literal'") from exc
        if object_kind is ClaimObjectKind.ENTITY:
            claim_object = _nonempty_text(self.object, "entity object")
        else:
            claim_object = _normalized_json(self.object, "literal object")
        confidence = self.confidence
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise TemporalVersionError("claim confidence must be a number from 0 to 1 or None")
            confidence = float(confidence)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise TemporalVersionError("claim confidence must be a finite number from 0 to 1")
        if not isinstance(self.provenance, Mapping):
            raise TemporalVersionError("claim provenance must be a JSON-compatible mapping")
        provenance = _normalized_json(dict(self.provenance), "provenance")
        statement_id = claim_statement_id(
            subject, predicate, _thaw_json(claim_object), object_kind=object_kind
        )
        claim_id = _identity("claim", {
            "statement_id": statement_id,
            "polarity": polarity.value,
            "agent": agent,
            "source": source,
            "world": world,
        })
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "predicate", predicate)
        object.__setattr__(self, "object", claim_object)
        object.__setattr__(self, "polarity", polarity)
        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "world", world)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "object_kind", object_kind)
        object.__setattr__(self, "statement_id", statement_id)
        object.__setattr__(self, "claim_id", claim_id)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible claim payload."""
        return {
            "claim_id": self.claim_id,
            "statement_id": self.statement_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": _thaw_json(self.object),
            "object_kind": self.object_kind.value,
            "polarity": self.polarity.value,
            "agent": self.agent,
            "source": self.source,
            "confidence": self.confidence,
            "world": self.world,
            "provenance": _thaw_json(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Claim":
        """Decode and authenticate a canonical claim payload."""
        if not isinstance(value, Mapping):
            raise TemporalVersionError("claim payload must be an object")
        try:
            claim = cls(
                subject=value["subject"],
                predicate=value["predicate"],
                object=value["object"],
                object_kind=value["object_kind"],
                polarity=value["polarity"],
                agent=value["agent"],
                source=value.get("source"),
                confidence=value.get("confidence"),
                world=value["world"],
                provenance=value.get("provenance", {}),
            )
        except KeyError as exc:
            raise TemporalVersionError("claim payload is incomplete") from exc
        if value.get("statement_id") != claim.statement_id or value.get("claim_id") != claim.claim_id:
            raise TemporalVersionError("claim payload identity does not match its contents")
        return claim


__all__ = [
    "Claim",
    "ClaimObjectKind",
    "ClaimPolarity",
    "ClaimStatus",
    "claim_statement_id",
]
