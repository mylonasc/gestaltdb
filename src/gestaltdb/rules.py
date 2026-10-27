"""Immutable Horn rules, stratified negation values and evaluation results."""

from __future__ import annotations

import builtins
import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping, cast

from .temporal import TemporalInstant
from .versioning import TemporalVersionError, normalize_version_id


class RuleError(TemporalVersionError):
    """Base error for invalid rule catalogs and evaluations.

    Sibling of :class:`gestaltdb.reasoning.model.ReasoningError` under
    :class:`TemporalVersionError` (both are ``ValueError``). Callers that
    need to catch every bounded-evaluation failure must catch
    ``(ReasoningError, TemporalVersionError)`` or ``ValueError``;
    ``except ReasoningError`` alone misses rule/modal bounds.
    """


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
        builtins.object.__setattr__(self, "subject", _term(self.subject))
        builtins.object.__setattr__(self, "predicate", _term(self.predicate, predicate=True))
        builtins.object.__setattr__(self, "object", _term(self.object))

    @classmethod
    def from_value(cls, value: builtins.object) -> "RuleAtom":
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            raise RuleError("rule atoms must be three-item tuples or lists")
        return cls(value[0], value[1], value[2])

    def to_tuple(self) -> tuple[str, str, str]:
        return self.subject, self.predicate, self.object


@dataclass(frozen=True)
class RuleVersion:
    """One immutable system-time version of a named safe Horn rule.

    ``when`` holds positive body atoms. ``not_when`` holds closed-world
    absence atoms whose variables must already be bound by ``when``. ``closed``
    declares predicates treated as complete for negation; the active program's
    effective scope is the union of these declarations, checked at creation.
    """

    rule_id: str
    version_id: str
    when: tuple[RuleAtom, ...]
    then: RuleAtom
    system_time: TemporalInstant
    catalog_ordinal: int
    not_when: tuple[RuleAtom, ...] = ()
    closed: tuple[str, ...] = ()

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
        if isinstance(self.not_when, (str, bytes)):
            raise RuleError("negated rule body must be an iterable of atoms")
        if isinstance(self.closed, (str, bytes)):
            raise RuleError("closed-world scope must be an iterable of predicates")
        negated = tuple(
            atom if isinstance(atom, RuleAtom) else RuleAtom.from_value(atom)
            for atom in self.not_when
        )
        closed = tuple(self.closed)
        for predicate in closed:
            if not isinstance(predicate, str) or not predicate:
                raise RuleError("closed-world predicates must be non-empty strings")
            if predicate.startswith("?"):
                raise RuleError("closed-world predicates must be constants")
        body_variables = {
            term
            for atom in body
            for term in (atom.subject, atom.object)
            if term.startswith("?")
        }
        negated_variables = {
            term
            for atom in negated
            for term in (atom.subject, atom.object)
            if term.startswith("?")
        }
        unsafe_negation = sorted(negated_variables - body_variables)
        if unsafe_negation:
            raise RuleError(
                "unsafe negated variables are not bound by the positive body: "
                + ", ".join(unsafe_negation)
            )
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
        object.__setattr__(self, "not_when", negated)
        object.__setattr__(self, "closed", tuple(sorted(set(closed))))

    def to_dict(self) -> dict[str, object]:
        value = {
            "rule_id": self.rule_id,
            "version_id": self.version_id,
            "when": [list(atom.to_tuple()) for atom in self.when],
            "then": list(self.then.to_tuple()),
            "system_time_us": self.system_time.epoch_microseconds,
            "catalog_ordinal": self.catalog_ordinal,
        }
        # Omit empty fields so pre-negation catalogs keep their exact shape.
        if self.not_when:
            value["not_when"] = [list(atom.to_tuple()) for atom in self.not_when]
        if self.closed:
            value["closed"] = list(self.closed)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleVersion":
        if not isinstance(value, Mapping):
            raise RuleError("rule version payload must be an object")
        try:
            when = value["when"]
            not_when = value.get("not_when", ())
            closed = value.get("closed", ())
            if not isinstance(when, list) or not isinstance(not_when, (list, tuple)):
                raise TypeError
            if not isinstance(closed, (list, tuple)):
                raise TypeError
            return cls(
                rule_id=cast(str, value["rule_id"]),
                version_id=cast(str, value["version_id"]),
                when=tuple(RuleAtom.from_value(atom) for atom in when),
                then=RuleAtom.from_value(value["then"]),
                system_time=TemporalInstant(cast(int, value["system_time_us"])),
                catalog_ordinal=cast(int, value["catalog_ordinal"]),
                not_when=tuple(RuleAtom.from_value(atom) for atom in not_when),
                closed=cast(tuple[str, ...], tuple(closed)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError("invalid rule version payload") from exc


@dataclass(frozen=True)
class RuleAbsence:
    """One ground closed-world absence check used as a negated premise.

    This is recorded evidence that no matching positive claim was visible at
    the pinned evaluation horizon. It is not a negative claim, a denial, or a
    claim version, and it carries no version identity.
    """

    position: int
    predicate: str
    subject: str
    object: str
    scope: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise RuleError("absence position must be a non-negative integer")
        if not isinstance(self.predicate, str) or not self.predicate:
            raise RuleError("absence predicate must be a non-empty string")
        for name, value in (("subject", self.subject), ("object", self.object)):
            if not isinstance(value, str) or not value:
                raise RuleError(f"absence {name} must be a non-empty string")
        scope = tuple(self.scope)
        if not scope:
            raise RuleError("absence scope must be a non-empty tuple of predicates")
        for predicate in scope:
            if not isinstance(predicate, str) or not predicate:
                raise RuleError("absence scope predicates must be non-empty strings")
        builtins.object.__setattr__(self, "scope", tuple(sorted(set(scope))))

    def to_dict(self) -> dict[str, builtins.object]:
        return {
            "position": self.position,
            "predicate": self.predicate,
            "subject": self.subject,
            "object": self.object,
            "scope": list(self.scope),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, builtins.object]) -> "RuleAbsence":
        if not isinstance(value, Mapping):
            raise RuleError("absence payload must be an object")
        try:
            return cls(
                position=cast(int, value["position"]),
                predicate=cast(str, value["predicate"]),
                subject=cast(str, value["subject"]),
                object=cast(str, value["object"]),
                scope=tuple(cast(Iterable[str], value["scope"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError("invalid absence payload") from exc


def absence_node_id(rule_version_id: str, absence: RuleAbsence) -> str:
    """Return the deterministic explanation node ID for one absence check."""
    payload = json.dumps(
        [
            normalize_version_id(rule_version_id),
            absence.position,
            absence.predicate,
            absence.subject,
            absence.object,
            list(absence.scope),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "absence:sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RuleJustification:
    """A rule version, ordered positive premises and any absence checks."""

    rule_version_id: str
    premise_version_ids: tuple[str, ...]
    absences: tuple[RuleAbsence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_version_id", normalize_version_id(self.rule_version_id))
        premises = tuple(normalize_version_id(value) for value in self.premise_version_ids)
        if not premises:
            raise RuleError("a rule justification requires at least one premise")
        absences = tuple(self.absences)
        if not all(isinstance(item, RuleAbsence) for item in absences):
            raise RuleError("justification absences must be RuleAbsence values")
        positions = [item.position for item in absences]
        if len(set(positions)) != len(positions):
            raise RuleError("justification absences must have unique positions")
        object.__setattr__(self, "premise_version_ids", premises)
        object.__setattr__(self, "absences", tuple(sorted(absences, key=lambda item: item.position)))

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "rule_version_id": self.rule_version_id,
            "premise_version_ids": list(self.premise_version_ids),
        }
        if self.absences:
            value["absences"] = [item.to_dict() for item in self.absences]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleJustification":
        if not isinstance(value, Mapping):
            raise RuleError("justification payload must be an object")
        try:
            absences = value.get("absences", ())
            if not isinstance(absences, (list, tuple)):
                raise TypeError
            return cls(
                rule_version_id=cast(str, value["rule_version_id"]),
                premise_version_ids=tuple(cast(Iterable[str], value["premise_version_ids"])),
                absences=tuple(RuleAbsence.from_dict(item) for item in absences),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError("invalid justification payload") from exc


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
    rule_id: object, when: Iterable[object], then: object, *,
    version_id: str, system_time: TemporalInstant, catalog_ordinal: int,
    not_when: Iterable[object] = (), closed: Iterable[object] = (),
) -> RuleVersion:
    """Validate a public rule definition and return its immutable version."""
    if isinstance(when, (str, bytes)):
        raise RuleError("rule body must be an iterable of atoms")
    if isinstance(not_when, (str, bytes)):
        raise RuleError("negated rule body must be an iterable of atoms")
    if isinstance(closed, (str, bytes)):
        raise RuleError("closed-world scope must be an iterable of predicates")
    try:
        atoms = tuple(RuleAtom.from_value(atom) for atom in when)
    except TypeError as exc:
        raise RuleError("rule body must be an iterable of atoms") from exc
    try:
        negated = tuple(RuleAtom.from_value(atom) for atom in not_when)
    except TypeError as exc:
        raise RuleError("negated rule body must be an iterable of atoms") from exc
    try:
        closed_values = tuple(closed)
    except TypeError as exc:
        raise RuleError("closed-world scope must be an iterable of predicates") from exc
    return RuleVersion(
        rule_id=cast(str, rule_id),
        version_id=version_id,
        when=atoms,
        then=RuleAtom.from_value(then),
        system_time=system_time,
        catalog_ordinal=catalog_ordinal,
        not_when=negated,
        closed=cast(tuple[str, ...], closed_values),
    )


def stratify_rules(rules: Iterable[RuleVersion]) -> tuple[tuple[str, ...], ...]:
    """Return a deterministic stratum schedule or raise on unstratifiable rules.

    Positive dependencies form the recursion components. Every negative edge
    must point strictly downward: a negative edge inside one positive component,
    or any remaining cycle after ordering, fails closed. Predicates without
    rules keep their positions as zero-round components. The effective
    closed-world scope is the union of all active rule declarations.
    """
    rules = tuple(sorted(rules, key=lambda rule: rule.rule_id))
    if not all(isinstance(rule, RuleVersion) for rule in rules):
        raise RuleError("stratification requires RuleVersion values")
    closed = {predicate for rule in rules for predicate in rule.closed}
    graph: dict[str, set[str]] = {}
    negative: dict[str, set[str]] = {}
    for rule in rules:
        head = rule.then.predicate
        graph.setdefault(head, set())
        negative.setdefault(head, set())
        for atom in rule.when:
            graph.setdefault(atom.predicate, set()).add(head)
        for atom in rule.not_when:
            if atom.predicate not in closed:
                raise RuleError(
                    f"negation over {atom.predicate!r} requires a closed-world declaration"
                )
            negative[head].add(atom.predicate)
            graph.setdefault(atom.predicate, set())

    # Iterative Kosaraju over positive dependencies only.
    visited: set[str] = set()
    finished: list[str] = []
    for node in sorted(graph):
        if node in visited:
            continue
        visited.add(node)
        stack = [(node, iter(sorted(graph[node])))]
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(current)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(graph[child]))))
    visited.clear()
    components: list[tuple[str, ...]] = []
    owner: dict[str, int] = {}
    for node in reversed(finished):
        if node in visited:
            continue
        visited.add(node)
        members = [node]
        pending = [node]
        while pending:
            current = pending.pop()
            for parent, dependents in graph.items():
                if current in dependents and parent not in visited:
                    visited.add(parent)
                    members.append(parent)
                    pending.append(parent)
        for member in members:
            owner[member] = len(components)
        components.append(tuple(sorted(members)))

    for head, targets in negative.items():
        if owner[head] in {owner[target] for target in targets}:
            raise RuleError(
                "negative dependency cycle: "
                f"{head!r} negatively depends on its own stratum"
            )
    before: dict[int, set[int]] = {index: set() for index in range(len(components))}
    for rule in rules:
        head_index = owner[rule.then.predicate]
        for atom in (*rule.when, *rule.not_when):
            before[head_index].add(owner[atom.predicate])
    remaining = {index: set(deps) - {index} for index, deps in before.items()}
    ordered: list[int] = []
    ready = sorted(
        [index for index, deps in remaining.items() if not deps],
        key=lambda index: components[index],
    )
    while ready:
        current_index = ready.pop(0)
        ordered.append(current_index)
        for index, deps in remaining.items():
            if current_index in deps:
                deps.discard(current_index)
                if not deps and index not in ordered and index not in ready:
                    ready.append(index)
        ready.sort(key=lambda index: components[index])
    if len(ordered) != len(components):
        stuck = [components[index] for index in range(len(components)) if index not in ordered]
        raise RuleError(
            "negative dependency cycle involving "
            + ", ".join(sorted({name for component in stuck for name in component}))
        )
    return tuple(components[index] for index in ordered)


__all__ = [
    "ClaimExplanation",
    "ExplanationEdge",
    "ExplanationNode",
    "RuleAbsence",
    "RuleAtom",
    "RuleError",
    "RuleEvaluationLimitError",
    "RuleJustification",
    "RuleRunResult",
    "RuleVersion",
    "TruthMaintenanceResult",
    "absence_node_id",
    "stratify_rules",
]
