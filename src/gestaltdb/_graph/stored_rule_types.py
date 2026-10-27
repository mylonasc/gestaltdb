"""Existing stored-rule work records, distinct from standalone reasoning IR."""

from typing import Literal, TypeAlias, TypedDict, TypeVar

from ..rules import RuleAbsence
from ..temporal import TemporalInterval
from ..versioning import ClaimVersion


ConclusionKey: TypeAlias = tuple[str, str, str, str]
SupportReference: TypeAlias = tuple[Literal["version"], str] | tuple[Literal["derived"], ConclusionKey]
ReferenceT = TypeVar("ReferenceT")
Justification: TypeAlias = tuple[str, tuple[ReferenceT, ...], tuple[RuleAbsence, ...]]
JoinRow: TypeAlias = tuple[dict[str, str], tuple[ReferenceT, ...], TemporalInterval | None, str | None, bool]
AbsenceOrder: TypeAlias = tuple[tuple[int, str, str, str, tuple[str, ...]], ...]


class RuleFact(TypedDict):
    subject: str
    predicate: str
    object: str
    world: str
    valid: TemporalInterval


class RunFact(RuleFact):
    version_id: str


class MaintenanceFact(RuleFact):
    ref: SupportReference


class RunOutput(RuleFact):
    existing: ClaimVersion | None
    version_id: str
    fact: RunFact | None
    justifications: set[Justification[str]]


class MaintenanceOutput(RuleFact):
    supports: set[Justification[SupportReference]]
    fact: MaintenanceFact | None


class EncodedJustification(TypedDict):
    """Fields consumed after RuleJustification.to_dict; extra keys are retained."""

    rule_version_id: str
    premise_version_ids: list[str]
