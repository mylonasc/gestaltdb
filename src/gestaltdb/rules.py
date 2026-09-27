"""Immutable positive Horn rules and rule-evaluation result values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .temporal import TemporalInstant
from .versioning import TemporalVersionError, normalize_version_id


class RuleError(TemporalVersionError):
    """Base error for invalid rule catalogs and evaluations."""


class RuleEvaluationLimitError(RuleError):
    """Raised before persistence when a rule run exceeds a resource bound."""


def _term(value: object, *, predicate: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise RuleError("rule terms must be non-empty strings")
    value.encode("utf-8")
    if value.startswith("?") and len(value) == 1:
        raise RuleError("rule variables must have a name after '?'")
    if predicate and value.startswith("?"):
        raise RuleError("rule predicates must be constants")
    return value


@dataclass(frozen=True)
class RuleAtom:
    """One ``(subject, predicate, object)`` atom in a positive Horn rule."""

    subject: str
    predicate: str
    object: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _term(self.subject))
        object.__setattr__(self, "predicate", _term(self.predicate, predicate=True))
        object.__setattr__(self, "object", _term(self.object))

    @classmethod
    def from_value(cls, value: object) -> "RuleAtom":
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            raise RuleError("rule atoms must be three-item tuples or lists")
        return cls(value[0], value[1], value[2])

    def to_tuple(self) -> tuple[str, str, str]:
        return self.subject, self.predicate, self.object


@dataclass(frozen=True)
class RuleVersion:
    """One immutable system-time version of a named safe positive Horn rule."""

    rule_id: str
    version_id: str
    when: tuple[RuleAtom, ...]
    then: RuleAtom
    system_time: TemporalInstant
    catalog_ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id:
            raise RuleError("rule_id must be a non-empty string")
        self.rule_id.encode("utf-8")
        object.__setattr__(self, "version_id", normalize_version_id(self.version_id))
        body = tuple(
            atom if isinstance(atom, RuleAtom) else RuleAtom.from_value(atom)
            for atom in self.when
        )
        if not body:
            raise RuleError("rule body must contain at least one atom")
        head = self.then if isinstance(self.then, RuleAtom) else RuleAtom.from_value(self.then)
        body_variables = {
            term
            for atom in body
            for term in (atom.subject, atom.object)
            if term.startswith("?")
        }
        head_variables = {
            term for term in (head.subject, head.object) if term.startswith("?")
        }
        unsafe = sorted(head_variables - body_variables)
        if unsafe:
            raise RuleError(
                "unsafe rule head variables are not bound by the body: " + ", ".join(unsafe)
            )
        if not isinstance(self.system_time, TemporalInstant):
            raise RuleError("rule system_time must be a TemporalInstant")
        if (
            isinstance(self.catalog_ordinal, bool)
            or not isinstance(self.catalog_ordinal, int)
            or self.catalog_ordinal < 1
        ):
            raise RuleError("rule catalog_ordinal must be a positive integer")
        object.__setattr__(self, "when", body)
        object.__setattr__(self, "then", head)

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "version_id": self.version_id,
            "when": [list(atom.to_tuple()) for atom in self.when],
            "then": list(self.then.to_tuple()),
            "system_time_us": self.system_time.epoch_microseconds,
            "catalog_ordinal": self.catalog_ordinal,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleVersion":
        if not isinstance(value, Mapping):
            raise RuleError("rule version payload must be an object")
        try:
            when = value["when"]
            if not isinstance(when, list):
                raise TypeError
            return cls(
                rule_id=value["rule_id"],
                version_id=value["version_id"],
                when=tuple(RuleAtom.from_value(atom) for atom in when),
                then=RuleAtom.from_value(value["then"]),
                system_time=TemporalInstant(value["system_time_us"]),
                catalog_ordinal=value["catalog_ordinal"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError("invalid rule version payload") from exc


@dataclass(frozen=True)
class RuleJustification:
    """A rule version and the ordered claim versions that satisfied its body."""

    rule_version_id: str
    premise_version_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_version_id", normalize_version_id(self.rule_version_id))
        premises = tuple(normalize_version_id(value) for value in self.premise_version_ids)
        if not premises:
            raise RuleError("a rule justification requires at least one premise")
        object.__setattr__(self, "premise_version_ids", premises)

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_version_id": self.rule_version_id,
            "premise_version_ids": list(self.premise_version_ids),
        }


@dataclass(frozen=True)
class RuleRunResult:
    """Summary of one bounded rule run and its persisted claim versions."""

    iterations: int
    derived_count: int
    justification_count: int
    commit_id: int | None
    versions: tuple[object, ...]


@dataclass(frozen=True)
class TruthMaintenanceResult:
    """Summary of one bounded incremental truth-maintenance pass."""

    input_commit_id: int
    commit_id: int | None
    asserted_count: int
    corrected_count: int
    retracted_count: int
    support_count: int
    versions: tuple[object, ...]


@dataclass(frozen=True)
class ExplanationNode:
    """One immutable claim or rule version in an explanation graph."""

    node_id: str
    kind: str
    value: object


@dataclass(frozen=True)
class ExplanationEdge:
    """A directed derivation or premise edge in an explanation graph."""

    source_id: str
    target_id: str
    relationship: str
    premise_ordinal: int | None = None


@dataclass(frozen=True)
class ClaimExplanation:
    """A finite historical explanation rooted at one visible claim version."""

    claim_id: str
    root_version_id: str
    valid_time: TemporalInstant
    system_time: TemporalInstant
    nodes: tuple[ExplanationNode, ...]
    edges: tuple[ExplanationEdge, ...]
    truncated: bool = False


def normalize_rule_definition(
    rule_id: object, when: Iterable[object], then: object, *, version_id: str,
    system_time: TemporalInstant, catalog_ordinal: int,
) -> RuleVersion:
    """Validate a public rule definition and return its immutable version."""
    if isinstance(when, (str, bytes)):
        raise RuleError("rule body must be an iterable of atoms")
    try:
        atoms = tuple(RuleAtom.from_value(atom) for atom in when)
    except TypeError as exc:
        raise RuleError("rule body must be an iterable of atoms") from exc
    return RuleVersion(
        rule_id=rule_id,
        version_id=version_id,
        when=atoms,
        then=RuleAtom.from_value(then),
        system_time=system_time,
        catalog_ordinal=catalog_ordinal,
    )


__all__ = [
    "ClaimExplanation",
    "ExplanationEdge",
    "ExplanationNode",
    "RuleAtom",
    "RuleError",
    "RuleEvaluationLimitError",
    "RuleJustification",
    "RuleRunResult",
    "RuleVersion",
    "TruthMaintenanceResult",
]
