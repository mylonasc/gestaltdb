"""Snapshot-local entity equality: canonical representatives, never physical merges.

``canonicalize`` rebuilds union-find state from scratch on every call over one
explicit fact set, so removing support can only split classes on the next
rebuild — stale unions are unrepresentable. Literals never merge (value
identity already decides them); no unique-name assumption is made. Functional
properties generate explicit ``SameAs`` links for later inspection instead of
silently rewriting facts. Temporal scoping belongs to RE-20; treat every
mapping as valid only for its evaluated snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, ReasoningLimitError, _text, check_cancelled
from .terms import Literal
from .typed import TypedFact, _sort_key


@dataclass(frozen=True, order=True)
class SameAs:
    """One explicit individual-equality assertion; order-canonical."""

    left: str
    right: str

    def __post_init__(self) -> None:
        _text(self.left, "left")
        _text(self.right, "right")
        if self.left == self.right:
            raise ReasoningError("sameAs links require two distinct individuals")
        if self.left > self.right:
            left, right = self.left, self.right
            object.__setattr__(self, "left", right)
            object.__setattr__(self, "right", left)


@dataclass(frozen=True, order=True)
class DifferentFrom:
    """One explicit individual-inequality assertion; order-canonical."""

    left: str
    right: str

    def __post_init__(self) -> None:
        _text(self.left, "left")
        _text(self.right, "right")
        if self.left == self.right:
            raise ReasoningError("differentFrom links require two distinct individuals")
        if self.left > self.right:
            left, right = self.left, self.right
            object.__setattr__(self, "left", right)
            object.__setattr__(self, "right", left)


@dataclass(frozen=True)
class FunctionalProperty:
    """A property whose single subject admits at most one value per snapshot.

    Conflicting values generate explicit ``SameAs`` links for inspection;
    nothing is merged implicitly and no equality is inferred silently.
    """

    property: str

    def __post_init__(self) -> None:
        _text(self.property, "property")


@dataclass(frozen=True)
class EqualityScope:
    """Opaque pinned-context identity for one canonicalization run."""

    scope_id: str

    def __post_init__(self) -> None:
        _text(self.scope_id, "scope_id")


@dataclass(frozen=True)
class InequalityConflict:
    """A differentFrom pair (or direct contradiction) inside one class."""

    left: str
    right: str
    representative: str
    evidence: tuple[SameAs | DifferentFrom, ...]

    def __post_init__(self) -> None:
        for name in ("left", "right", "representative"):
            _text(getattr(self, name), name)
        if not isinstance(self.evidence, (tuple, list)) or not self.evidence:
            raise ReasoningError("conflict evidence must be non-empty")
        if not all(isinstance(link, (SameAs, DifferentFrom)) for link in self.evidence):
            raise ReasoningError("conflict evidence must contain SameAs/DifferentFrom links")
        object.__setattr__(self, "evidence", tuple(sorted(
            set(self.evidence), key=lambda link: (type(link).__name__, link.left, link.right))))


@dataclass(frozen=True)
class CanonicalMapping:
    """Deterministic representatives with member aliases, justifications, conflicts."""

    scope: EqualityScope
    classes: tuple[tuple[str, tuple[str, ...]], ...]
    justifications: tuple[tuple[str, tuple[SameAs, ...]], ...]
    conflicts: tuple[InequalityConflict, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EqualityScope):
            raise ReasoningError("scope must be an EqualityScope")
        if not isinstance(self.classes, (tuple, list)):
            raise ReasoningError("classes must be a tuple or list")
        seen: set[str] = set()
        normalized = []
        for representative, members in self.classes:
            _text(representative, "representative")
            if not isinstance(members, (tuple, list)) or not members:
                raise ReasoningError("class members must be non-empty")
            ordered = tuple(sorted(set(members)))
            if representative != ordered[0]:
                raise ReasoningError("representative must be the smallest member")
            if any(member in seen for member in ordered):
                raise ReasoningError("classes must partition members")
            seen.update(ordered)
            normalized.append((representative, ordered))
        object.__setattr__(self, "classes", tuple(sorted(normalized)))
        if not isinstance(self.justifications, (tuple, list)):
            raise ReasoningError("justifications must be a tuple or list")
        justified = {representative for representative, _ in self.classes}
        table = []
        for representative, links in self.justifications:
            _text(representative, "justified representative")
            if representative not in justified:
                raise ReasoningError("justifications must reference known classes")
            if not isinstance(links, (tuple, list)) or not all(isinstance(link, SameAs) for link in links):
                raise ReasoningError("justifications must list SameAs links")
            if representative in [done for done, _ in table]:
                raise ReasoningError("duplicate justification")
            table.append((representative, tuple(sorted(set(links)))))
        object.__setattr__(self, "justifications", tuple(sorted(table)))
        if not isinstance(self.conflicts, (tuple, list)) or not all(
                isinstance(item, InequalityConflict) for item in self.conflicts):
            raise ReasoningError("conflicts must contain only InequalityConflict values")
        object.__setattr__(self, "conflicts", tuple(sorted(
            set(self.conflicts), key=lambda item: (item.left, item.right, item.representative))))

    @property
    def digest(self) -> str:
        import hashlib
        import json

        payload = ["gestaltdb-equality-v1", self.scope.scope_id,
                   [[representative, list(members)] for representative, members in self.classes],
                   [[representative, [[link.left, link.right] for link in links]]
                    for representative, links in self.justifications],
                   [[item.left, item.right, item.representative,
                     [[link.left, link.right] for link in item.evidence]] for item in self.conflicts]]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def representative(self, entity: str) -> str:
        """Canonical representative, or the entity itself when unmerged."""
        _text(entity, "entity")
        for representative, members in self.classes:
            if entity in members:
                return representative
        return entity

    def members(self, representative: str) -> tuple[str, ...]:
        """All external aliases merged into one representative."""
        for known, known_members in self.classes:
            if known == representative:
                return known_members
        return (representative,)

    def justification(self, representative: str) -> tuple[SameAs, ...]:
        for known, links in self.justifications:
            if known == representative:
                return links
        return ()

    def rewrite(self, facts: Iterable[TypedFact]) -> tuple[TypedFact, ...]:
        """Replace entity values with representatives; literals pass through."""
        try:
            items = list(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be iterable") from exc
        rewritten = []
        for fact in items:
            if not isinstance(fact, TypedFact):
                raise ReasoningError("facts must contain only TypedFact values")
            rewritten.append(TypedFact(fact.predicate, tuple(
                self.representative(value) if isinstance(value, str) else value for value in fact.values)))
        return tuple(sorted(set(rewritten), key=_sort_key))


def canonicalize(facts: Iterable[TypedFact], links: Iterable[SameAs | DifferentFrom | FunctionalProperty] = (),
                 *, scope: EqualityScope, max_entities: int = 100_000,
                 cancel=None) -> CanonicalMapping:
    """Rebuild canonical classes from scratch over one explicit fact set.

    Union-find state is invocation-local and discarded on return, so support
    removal can only split classes at the next rebuild. Functional conflicts
    first generate explicit links, then participate like asserted ones.
    """
    if not isinstance(scope, EqualityScope):
        raise ReasoningError("scope must be an EqualityScope")
    if isinstance(max_entities, bool) or not isinstance(max_entities, int) or max_entities < 1:
        raise ReasoningError("max_entities must be a positive integer")
    check_cancelled(cancel)
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(fact, TypedFact) for fact in items):
        raise ReasoningError("facts must contain only TypedFact values")
    try:
        asserted = list(links)
    except TypeError as exc:
        raise ReasoningError("links must be iterable") from exc
    generated: list[SameAs] = []
    different: list[DifferentFrom] = []
    functional: list[str] = []
    for link in asserted:
        if isinstance(link, SameAs):
            generated.append(link)
        elif isinstance(link, DifferentFrom):
            different.append(link)
        elif isinstance(link, FunctionalProperty):
            functional.append(link.property)
        else:
            raise ReasoningError("links must be SameAs, DifferentFrom or FunctionalProperty values")
    for predicate in functional:
        by_subject: dict[str, list[str]] = {}
        for fact in items:
            if fact.predicate == predicate and len(fact.values) == 2 and all(
                    isinstance(value, str) for value in fact.values):
                by_subject.setdefault(fact.values[0], [])
                if fact.values[1] not in by_subject[fact.values[0]]:
                    by_subject[fact.values[0]].append(fact.values[1])
        for subject, values in sorted(by_subject.items()):
            for first, second in zip(values, values[1:]):
                generated.append(SameAs(first, second))
    entities: set[str] = set()
    for fact in items:
        for value in fact.values:
            if isinstance(value, str):
                entities.add(value)
    for link in (*generated, *different):
        entities.update((link.left, link.right))
    if len(entities) > max_entities:
        raise ReasoningLimitError(f"canonicalization exceeded max_entities={max_entities}")
    parent = {entity: entity for entity in entities}
    size = {entity: 1 for entity in entities}
    evidence: dict[str, list[SameAs]] = {entity: [] for entity in entities}

    def find(entity: str) -> str:
        while parent[entity] != entity:
            parent[entity] = parent[parent[entity]]
            entity = parent[entity]
        return entity

    count = 0
    for link in sorted(set(generated)):
        count += 1
        if count % 1024 == 0:
            check_cancelled(cancel)
        left, right = find(link.left), find(link.right)
        if left == right:
            continue
        keep, drop = (left, right) if size[left] >= size[right] else (right, left)
        parent[drop] = keep
        size[keep] += size[drop]
        evidence[keep].extend(evidence[drop])
        evidence[keep].append(link)
        del evidence[drop]
    members_by_root: dict[str, set[str]] = {}
    for entity in entities:
        members_by_root.setdefault(find(entity), set()).add(entity)
    justifications = tuple((min(members), tuple(sorted(set(evidence[root]))))
                           for root, members in sorted(members_by_root.items()))
    conflicts: list[InequalityConflict] = []
    for link in sorted(set(different)):
        if find(link.left) == find(link.right):
            root = find(link.left)
            support = sorted(set(evidence[root]) | {link},
                             key=lambda item: (type(item).__name__, item.left, item.right))
            conflicts.append(InequalityConflict(link.left, link.right, min(members_by_root[root]),
                                                tuple(support)))
    return CanonicalMapping(scope, tuple((min(members), tuple(sorted(members)))
                                         for _, members in sorted(members_by_root.items())),
                            justifications, tuple(conflicts))
