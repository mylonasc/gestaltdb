"""Immutable validated type hierarchies for relations and node labels.

``RelationHierarchy`` maps child relation names to their direct parents, so a
sampler asked for ``Transport_Edge`` can expand to ``Ocean_Freight``,
``Air_Freight`` and ``Rail``. ``LabelHierarchy`` applies the same contract to
native node labels, so an opt-in Cypher query for ``:Transport_Edge`` can match
nodes labelled with any transitive sub-type.

Both classes are standalone: they validate an explicit schema of external name
strings and couple to no ontology compiler, storage backend or query planner.
Names absent as keys are roots. Lookups of unknown names return the name itself,
so a hierarchy only adds matches for declared children.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _check_name(value: object, role: str, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{role} {kind} names must be non-empty strings")
    return value


class TypeHierarchy:
    """Immutable validated parent/child name hierarchy.

    Args:
        parents: Mapping of child name to its direct parent names. Names absent
            as keys are roots. Duplicate parents for one child are deduplicated;
            duplicate child entries are rejected.

    Raises:
        ValueError: On empty names, self-parenting, duplicate child entries, or
            parent/child cycles.
    """

    _kind = "type"

    def __init__(self, parents: Mapping[str, Sequence[str]] | None = None):
        if parents is None:
            parents = {}
        if not isinstance(parents, Mapping):
            raise ValueError("parents must be a mapping of child to parent names")
        items = list(parents.items())
        children: dict[str, tuple[str, ...]] = {}
        for child, direct in items:
            _check_name(child, "child", self._kind)
            if child in children:
                raise ValueError(f"duplicate child {self._kind} entry: {child!r}")
            if isinstance(direct, (str, bytes)):
                raise ValueError(
                    f"parents of {child!r} must be a sequence, not a scalar string"
                )
            try:
                direct_list = list(direct)
            except TypeError as exc:
                raise ValueError(f"parents of {child!r} must be a sequence") from exc
            unique = tuple(
                dict.fromkeys(
                    _check_name(parent, "parent", self._kind) for parent in direct_list
                )
            )
            if child in unique:
                raise ValueError(f"{self._kind} cannot parent itself: {child!r}")
            children[child] = unique
        self._children = children
        self._descendants: dict[str, tuple[str, ...]] = {}
        for name in self._all_names():
            self._descendants[name] = self._compute_descendants(name, ())
        self._hash = hash((
            type(self),
            tuple(sorted(
                (child, parent)
                for child, direct in children.items()
                for parent in direct
            )),
        ))

    @classmethod
    def from_definitions(
        cls, definitions: Sequence[tuple[str, Sequence[str]]]
    ) -> "TypeHierarchy":
        """Build a hierarchy from ``(child, parents)`` pairs in order."""
        try:
            pairs = list(definitions)
        except TypeError as exc:
            raise ValueError(
                "definitions must be a sequence of (child, parents) pairs"
            ) from exc
        merged: dict[str, list[str]] = {}
        for pair in pairs:
            try:
                child, direct = pair
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "each definition must be a (child, parents) pair"
                ) from exc
            if child in merged:
                raise ValueError(f"duplicate child {cls._kind} entry: {child!r}")
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
            raise ValueError(f"{self._kind} hierarchy contains a cycle: {cycle}")
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

    def parents_of(self, name: str) -> tuple[str, ...]:
        """Return the direct parents of a name (empty for roots)."""
        _check_name(name, "query", self._kind)
        return self._children.get(name, ())

    def closure(self, name: str) -> tuple[str, ...]:
        """Return a name plus all its transitive sub-types."""
        _check_name(name, "query", self._kind)
        return (name, *self._descendants.get(name, ()))

    def expand(self, names: Sequence[str]) -> tuple[str, ...]:
        """Expand parent names to their closures, preserving first-seen order."""
        if isinstance(names, (str, bytes)):
            raise ValueError("queries must be a sequence, not a scalar string")
        try:
            values = list(names)
        except TypeError as exc:
            raise ValueError("queries must be a sequence") from exc
        expanded: list[str] = []
        for name in values:
            _check_name(name, "query", self._kind)
            for member in self.closure(name):
                if member not in expanded:
                    expanded.append(member)
        return tuple(expanded)

    def __len__(self) -> int:
        return len(self._all_names())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._descendants

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self._children == other._children

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._children!r})"


class RelationHierarchy(TypeHierarchy):
    """Immutable validated sub-property hierarchy for relation names."""

    _kind = "relation"


class LabelHierarchy(TypeHierarchy):
    """Immutable validated sub-type hierarchy for native node labels."""

    _kind = "label"


__all__ = ["LabelHierarchy", "RelationHierarchy", "TypeHierarchy"]
