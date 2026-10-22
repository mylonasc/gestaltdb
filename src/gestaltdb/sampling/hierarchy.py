"""Explicit sub-property hierarchies for sampler relation expansion.

A ``RelationHierarchy`` maps child relations to their direct parents
(``Ocean_Freight`` → ``Transport_Edge``). Samplers match literal relation
IDs, so querying a parent transparently expands to the parent plus every
transitive sub-property through ``SamplerSnapshot.expand_relations`` —
no manual enumeration of edge types, with fanout applying to the union
(documented in ``docs/typed-sampling.rst``).

This is intentionally standalone: it covers external relation-name
strings only, needs no ontology compiler, and adds no sampler-engine
parameters. Node sub-types and Cypher/planner integration remain open
follow-ups on the PropEnh-14 tracker.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _check_name(value: object, role: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{role} relation names must be non-empty strings")
    return value


class RelationHierarchy:
    """Immutable validated sub-property hierarchy.

    Args:
        parents: Mapping of child relation name to its direct parent names.
            Names absent as keys are roots. Duplicate parents for one child
            are deduplicated; duplicate child entries are rejected.

    Raises:
        ValueError: On empty names, self-parenting, duplicate child
            entries, or parent/child cycles.
    """

    def __init__(self, parents: Mapping[str, Sequence[str]] | None = None):
        if parents is None:
            parents = {}
        if not isinstance(parents, Mapping):
            raise ValueError("parents must be a mapping of child to parent names")
        items = list(parents.items())
        children: dict[str, tuple[str, ...]] = {}
        for child, direct in items:
            _check_name(child, "child")
            if child in children:
                raise ValueError(f"duplicate child relation entry: {child!r}")
            if isinstance(direct, (str, bytes)):
                raise ValueError(f"parents of {child!r} must be a sequence, not a scalar string")
            try:
                direct_list = list(direct)
            except TypeError as exc:
                raise ValueError(f"parents of {child!r} must be a sequence") from exc
            unique = tuple(dict.fromkeys(_check_name(parent, "parent") for parent in direct_list))
            if child in unique:
                raise ValueError(f"relation cannot parent itself: {child!r}")
            children[child] = unique
        self._children = children
        self._descendants: dict[str, tuple[str, ...]] = {}
        for name in self._all_names():
            self._descendants[name] = self._compute_descendants(name, ())
        self._hash = hash((RelationHierarchy, tuple(sorted(
            (child, parent) for child, direct in children.items() for parent in direct))))

    @classmethod
    def from_definitions(cls, definitions: Sequence[tuple[str, Sequence[str]]]) -> "RelationHierarchy":
        """Build a hierarchy from ``(child, parents)`` pairs in order."""
        try:
            pairs = list(definitions)
        except TypeError as exc:
            raise ValueError("definitions must be a sequence of (child, parents) pairs") from exc
        merged: dict[str, list[str]] = {}
        for pair in pairs:
            try:
                child, direct = pair
            except (TypeError, ValueError) as exc:
                raise ValueError("each definition must be a (child, parents) pair") from exc
            if child in merged:
                raise ValueError(f"duplicate child relation entry: {child!r}")
            merged[child] = list(direct) if not isinstance(direct, (str, bytes)) else direct
        return cls(merged)

    def _all_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for child, direct in self._children.items():
            seen.setdefault(child)
            for parent in direct:
                seen.setdefault(parent)
        return list(seen)

    def _compute_descendants(self, name: str, trail: tuple[str, ...]) -> tuple[str, ...]:
        if name in trail:
            cycle = " -> ".join((*trail, name))
            raise ValueError(f"relation hierarchy contains a cycle: {cycle}")
        found: list[str] = []
        for child, direct in self._children.items():
            if name in direct:
                found.append(child)
        ordered: list[str] = []
        for child in found:
            ordered.append(child)
            for descendant in self._compute_descendants(child, (*trail, name)):
                if descendant not in ordered:
                    ordered.append(descendant)
        return tuple(ordered)

    def parents_of(self, relation: str) -> tuple[str, ...]:
        """Return the direct parents of a relation (empty for roots)."""
        _check_name(relation, "query")
        return self._children.get(relation, ())

    def closure(self, relation: str) -> tuple[str, ...]:
        """Return a relation plus all its transitive sub-properties."""
        _check_name(relation, "query")
        return (relation, *self._descendants.get(relation, ()))

    def expand(self, relations: Sequence[str]) -> tuple[str, ...]:
        """Expand parent names to their closures, preserving first-seen order."""
        if isinstance(relations, (str, bytes)):
            raise ValueError("relations must be a sequence, not a scalar string")
        try:
            names = list(relations)
        except TypeError as exc:
            raise ValueError("relations must be a sequence") from exc
        expanded: list[str] = []
        for name in names:
            _check_name(name, "query")
            for member in self.closure(name):
                if member not in expanded:
                    expanded.append(member)
        return tuple(expanded)

    def __len__(self) -> int:
        return len(self._all_names())

    def __contains__(self, relation: object) -> bool:
        return isinstance(relation, str) and relation in self._descendants

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RelationHierarchy) and self._children == other._children

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"RelationHierarchy({self._children!r})"
