"""Immutable exact-version assertion ledger; adapters authenticate its context.

Logical facts are not evidence IDs. Retiring one version only retracts a fact
when its final active assertion disappears. Retired IDs remain bound to their
original fact; resurrection requires a new version ID. This module stores no
derived supports and does not authenticate a database or consume commit logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, ReasoningLimitError, _text
from .typed import TypedFact, _sort_key


@dataclass(frozen=True)
class AssertionVersion:
    version_id: str
    fact: TypedFact

    def __post_init__(self) -> None:
        _text(self.version_id, "version_id")
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("fact must be TypedFact")


@dataclass(frozen=True)
class EvidenceLedger:
    """Canonical active and retired versions in one adapter-selected scope.

    ``scope_id`` binds database/world/selection/mapping, not the changing source
    horizon. The session adapter must authenticate each transition's horizon.
    Limits include tombstones; reaching the bound requires an explicit rebuild
    or durable external history, never silently forgetting version identities.
    """

    scope_id: str
    versions: tuple[AssertionVersion, ...] = ()
    retired: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.scope_id, "scope_id")
        if not isinstance(self.versions, (tuple, list)) or not isinstance(self.retired, (tuple, list)):
            raise ReasoningError("versions and retired must be sequences")
        indexed = {}
        for item in self.versions:
            if not isinstance(item, AssertionVersion):
                raise ReasoningError("versions must contain AssertionVersion values")
            if item.version_id in indexed and indexed[item.version_id] != item:
                raise ReasoningError("conflicting version ID")
            indexed[item.version_id] = item
        for key in self.retired:
            _text(key, "retired version ID")
            if key not in indexed:
                raise ReasoningError("cannot retire an unknown version ID")
        object.__setattr__(self, "versions", tuple(indexed[key] for key in sorted(indexed)))
        object.__setattr__(self, "retired", tuple(sorted(set(self.retired))))

    @property
    def facts(self) -> tuple[TypedFact, ...]:
        retired = set(self.retired)
        return tuple(sorted({item.fact for item in self.versions if item.version_id not in retired}, key=_sort_key))

    def transition(self, *, scope_id: str, assertions: Iterable[AssertionVersion] = (),
                   retractions: Iterable[str] = (), max_versions: int = 100_000) -> LedgerTransition:
        """Apply a bounded atomic change and return logical set differences.

        A correction is an assertion plus retirement in the same call. An
        assertion retry for an already retired version never reactivates it.
        Unknown retractions fail closed: missing history needs reconciliation.
        """
        if scope_id != self.scope_id:
            raise ReasoningError("ledger scope mismatch; rebuild explicitly")
        if isinstance(max_versions, bool) or not isinstance(max_versions, int) or max_versions < 1:
            raise ReasoningError("max_versions must be a positive integer")
        if len(self.versions) > max_versions:
            raise ReasoningLimitError("ledger exceeds max_versions")
        indexed = {item.version_id: item for item in self.versions}
        retired = set(self.retired)
        for count, item in enumerate(assertions, 1):
            if count > max_versions:
                raise ReasoningLimitError("assertion input exceeds max_versions")
            if not isinstance(item, AssertionVersion):
                raise ReasoningError("assertions must contain AssertionVersion values")
            if item.version_id in indexed and indexed[item.version_id] != item:
                raise ReasoningError("conflicting version ID")
            indexed[item.version_id] = item
            if len(indexed) > max_versions:
                raise ReasoningLimitError("ledger exceeds max_versions")
        for count, key in enumerate(retractions, 1):
            if count > max_versions:
                raise ReasoningLimitError("retraction input exceeds max_versions")
            _text(key, "retraction version ID")
            if key not in indexed:
                raise ReasoningError("cannot retract an unknown version ID")
            retired.add(key)
        updated = EvidenceLedger(self.scope_id, tuple(indexed.values()), tuple(retired))
        before, after = set(self.facts), set(updated.facts)
        return LedgerTransition(updated, tuple(sorted(after - before, key=_sort_key)),
                                 tuple(sorted(before - after, key=_sort_key)))


@dataclass(frozen=True)
class LedgerTransition:
    ledger: EvidenceLedger
    inserted: tuple[TypedFact, ...]
    retracted: tuple[TypedFact, ...]
