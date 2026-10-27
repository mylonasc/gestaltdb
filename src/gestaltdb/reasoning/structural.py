"""Declarative structural validation under explicit completeness assumptions.

:class:`StructuralSuite` checks a finite typed closure for shape constraints
that :mod:`gestaltdb.reasoning.validation` does not cover: required
properties, min/max cardinalities, datatype restrictions and mutually
exclusive / exactly-one-of property groups. Validation is read-only and
non-mutating: missing fields never become logical contradictions, no facts
are inferred, and violations carry exact supporting facts plus constraint
identities.

Completeness is explicit and scoped. Open-world silence stays silent: only
entities that are members of a declared ``closed_classes`` entry are checked
for required, cardinality and exclusivity constraints. Datatype checks apply
to property facts whose subject is such a closed entity. Anything outside
the closed scope is ignored, so validating a partial export cannot flood the
report with absence assumptions the world does not grant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .model import ReasoningError, _text, check_cancelled
from .terms import KINDS, Literal
from .typed import TypedFact, _sort_key


@dataclass(frozen=True)
class RequiredField:
    """One mandatory property on members of one class."""

    class_name: str
    predicate: str

    def __post_init__(self) -> None:
        _text(self.class_name, "required class")
        _text(self.predicate, "required predicate")


@dataclass(frozen=True)
class CardinalityBound:
    """Min/max occurrence bound for one class/property pair.

    ``min_count`` is inclusive; ``max_count`` is inclusive or ``None`` for
    unbounded. Counting is over facts with the bound predicate whose first
    value is the entity.
    """

    class_name: str
    predicate: str
    min_count: int
    max_count: int | None

    def __post_init__(self) -> None:
        _text(self.class_name, "cardinality class")
        _text(self.predicate, "cardinality predicate")
        for name, value in (("min_count", self.min_count), ("max_count", self.max_count)):
            if value is None:
                if name == "min_count":
                    raise ReasoningError("min_count must be a non-negative integer")
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReasoningError(f"cardinality {name} must be a non-negative integer or None")
        if self.max_count is not None and self.min_count > self.max_count:
            raise ReasoningError("cardinality min_count must not exceed max_count")


@dataclass(frozen=True)
class DatatypeConstraint:
    """Literal-kind restriction for one class/property pair.

    Every object value (``values[1:]``) of a matching property fact must be
    a :class:`Literal` of the declared kind. Plain entity strings fail the
    check: they are references, not typed values.
    """

    class_name: str
    predicate: str
    kind: str

    def __post_init__(self) -> None:
        _text(self.class_name, "datatype class")
        _text(self.predicate, "datatype predicate")
        _text(self.kind, "datatype kind")
        if self.kind not in KINDS:
            raise ReasoningError(f"datatype kind must be one of {KINDS}")


@dataclass(frozen=True)
class ExclusiveGroup:
    """Mutually exclusive property group on members of one class.

    ``mode`` is ``"at-most-one"`` (zero or one of the predicates present)
    or ``"one-of"`` (exactly one present). Presence means at least one fact
    with that predicate on the entity.
    """

    class_name: str
    predicates: tuple[str, ...]
    mode: str = "one-of"

    def __post_init__(self) -> None:
        _text(self.class_name, "exclusive class")
        if not isinstance(self.predicates, (tuple, list)) or len(self.predicates) < 2:
            raise ReasoningError("exclusive predicates must list at least two properties")
        for predicate in self.predicates:
            _text(predicate, "exclusive predicate")
        if len(set(self.predicates)) != len(self.predicates):
            raise ReasoningError("exclusive predicates must be distinct")
        object.__setattr__(self, "predicates", tuple(sorted(self.predicates)))
        if self.mode not in ("one-of", "at-most-one"):
            raise ReasoningError("exclusive mode must be 'one-of' or 'at-most-one'")


@dataclass(frozen=True)
class StructuralSuite:
    """Closed-world validation scope: every check is explicit, nothing inferred.

    ``membership`` names the class-membership predicate the checks read.
    ``closed_classes`` declares which classes carry completeness: required,
    cardinality and exclusivity checks run only for entities typed as members
    of a closed class. Datatype checks apply to property facts on those same
    entities. An empty ``closed_classes`` tuple checks nothing and every
    report is clean.
    """

    membership: str
    closed_classes: tuple[str, ...] = ()
    required: tuple[RequiredField, ...] = ()
    cardinalities: tuple[CardinalityBound, ...] = ()
    datatypes: tuple[DatatypeConstraint, ...] = ()
    exclusive: tuple[ExclusiveGroup, ...] = ()

    def __post_init__(self) -> None:
        _text(self.membership, "membership predicate")
        if not isinstance(self.closed_classes, (tuple, list)):
            raise ReasoningError("closed_classes must be a tuple or list")
        for name in self.closed_classes:
            _text(name, "closed class")
        object.__setattr__(self, "closed_classes", tuple(sorted(set(self.closed_classes))))
        for field, kind in (("required", RequiredField), ("cardinalities", CardinalityBound),
                            ("datatypes", DatatypeConstraint), ("exclusive", ExclusiveGroup)):
            value = getattr(self, field)
            if not isinstance(value, (tuple, list)):
                raise ReasoningError(f"suite {field} must be a tuple or list")
            if not all(isinstance(item, kind) for item in value):
                raise ReasoningError(f"suite {field} must contain only {kind.__name__} values")
            object.__setattr__(self, field, tuple(value))

    @property
    def digest(self) -> str:
        """Structural identity of the validation scope."""
        import hashlib
        import json

        payload = ["gestaltdb-structural-v1", self.membership, list(self.closed_classes),
                   [[item.class_name, item.predicate] for item in self.required],
                   [[item.class_name, item.predicate, item.min_count, item.max_count]
                    for item in self.cardinalities],
                   [[item.class_name, item.predicate, item.kind] for item in self.datatypes],
                   [[item.class_name, list(item.predicates), item.mode] for item in self.exclusive]]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StructuralViolation:
    """One bounded structural conflict with exact supporting facts."""

    kind: str
    facts: tuple[TypedFact, ...]
    sources: tuple[str, ...]
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in ("missing-required", "cardinality-violation",
                             "datatype-violation", "exclusive-violation"):
            raise ReasoningError(f"unknown structural violation kind: {self.kind!r}")
        if not isinstance(self.facts, (tuple, list)) or not self.facts:
            raise ReasoningError("violation facts must be non-empty")
        if not all(isinstance(fact, TypedFact) for fact in self.facts):
            raise ReasoningError("violation facts must contain only TypedFact values")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))
        if not isinstance(self.sources, (tuple, list)) or not self.sources:
            raise ReasoningError("violation sources must be non-empty")
        for source in self.sources:
            _text(source, "violation source")
        object.__setattr__(self, "sources", tuple(sorted(set(self.sources))))
        _text(self.detail, "violation detail")


@dataclass(frozen=True)
class StructuralReport:
    """Deterministic violation list; ``truncated`` marks a hit budget."""

    violations: tuple[StructuralViolation, ...]
    truncated: bool
    scanned_facts: int

    def __post_init__(self) -> None:
        if not isinstance(self.violations, (tuple, list)):
            raise ReasoningError("violations must be a tuple or list")
        if not all(isinstance(item, StructuralViolation) for item in self.violations):
            raise ReasoningError("violations must contain only StructuralViolation values")
        if not isinstance(self.truncated, bool):
            raise ReasoningError("truncated must be a bool")
        if isinstance(self.scanned_facts, bool) or not isinstance(self.scanned_facts, int) or self.scanned_facts < 0:
            raise ReasoningError("scanned_facts must be a non-negative integer")
        object.__setattr__(self, "violations", tuple(sorted(
            set(self.violations),
            key=lambda item: (item.kind, [_sort_key(fact) for fact in item.facts]))))

    @property
    def clean(self) -> bool:
        return not self.violations and not self.truncated


def _emit(violations, limit, violation):
    if len(violations) >= limit:
        return True
    violations.append(violation)
    return len(violations) >= limit


def _entity_key(value) -> str:
    return value if isinstance(value, str) else repr(value)


def validate_structural(facts: Iterable[TypedFact], suite: StructuralSuite, *,
                        max_violations: int = 1000, cancel=None) -> StructuralReport:
    """Check a finite closure against the suite; never modifies or extends it.

    Facts stream in canonical order; violations are deterministic for
    identical inputs. ``max_violations`` bounds the report — hitting it sets
    ``truncated`` instead of failing. Cancellation raises without a report.

    Examples:
        Flag a missing required property without changing the facts::

            >>> from gestaltdb.reasoning.typed import TypedFact
            >>> from gestaltdb.reasoning.structural import (
            ...     RequiredField, StructuralSuite, validate_structural)
            >>> facts = [TypedFact("type", ["n", "Trade"])]
            >>> suite = StructuralSuite("type", closed_classes=["Trade"],
            ...     required=[RequiredField("Trade", "tradeId")])
            >>> report = validate_structural(facts, suite)
            >>> assert not report.clean
            >>> assert report.violations[0].kind == "missing-required"
            >>> assert len(facts) == 1

    Only entities typed as members of a declared closed class are checked;
    everything else keeps open-world silence. ``max_violations`` limits the
    report rather than input memory.
    """
    if not isinstance(suite, StructuralSuite):
        raise ReasoningError("suite must be a StructuralSuite")
    if isinstance(max_violations, bool) or not isinstance(max_violations, int) or max_violations < 1:
        raise ReasoningError("max_violations must be a positive integer")
    check_cancelled(cancel)
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be an iterable of TypedFact values") from exc
    if not all(isinstance(fact, TypedFact) for fact in items):
        raise ReasoningError("facts must contain only TypedFact values")
    ordered = sorted(set(items), key=_sort_key)
    violations: list[StructuralViolation] = []
    truncated = False
    closed = set(suite.closed_classes)
    members: dict[str | Literal, set[str]] = {}
    membership_fact: dict[str | Literal, dict[str, TypedFact]] = {}
    if not closed:
        return StructuralReport((), False, len(ordered))
    for index, fact in enumerate(ordered):
        if index % 1024 == 0:
            check_cancelled(cancel)
        if fact.predicate == suite.membership and len(fact.values) == 2 and isinstance(fact.values[1], str):
            entity, class_name = fact.values
            if class_name in closed:
                members.setdefault(entity, set()).add(class_name)
                membership_fact.setdefault(entity, {})[class_name] = fact
    by_subject: dict[object, dict[str, list[TypedFact]]] = {}
    for fact in ordered:
        if fact.values and isinstance(fact.values[0], str):
            by_subject.setdefault(fact.values[0], {}).setdefault(fact.predicate, []).append(fact)
    for entity in sorted(members, key=_entity_key):
        classes = members[entity]
        properties = by_subject.get(entity, {})
        for required in sorted(suite.required, key=lambda item: (item.class_name, item.predicate)):
            if required.class_name not in classes:
                continue
            if not properties.get(required.predicate):
                member = membership_fact[entity][required.class_name]
                truncated = _emit(violations, max_violations, StructuralViolation(
                    "missing-required", (member,),
                    (f"required:{required.class_name}:{required.predicate}",),
                    f"{entity!r} of class {required.class_name!r} "
                    f"is missing required property {required.predicate!r}"))
                if truncated:
                    return StructuralReport(tuple(violations), True, len(ordered))
        for cardinality in sorted(suite.cardinalities,
                                 key=lambda item: (item.class_name, item.predicate)):
            if cardinality.class_name not in classes:
                continue
            count = len(properties.get(cardinality.predicate, ()))
            if count < cardinality.min_count or (cardinality.max_count is not None
                                               and count > cardinality.max_count):
                supporting = tuple(properties.get(cardinality.predicate, ())) or (
                    membership_fact[entity][cardinality.class_name],)
                truncated = _emit(violations, max_violations, StructuralViolation(
                    "cardinality-violation", supporting,
                    (f"cardinality:{cardinality.class_name}:{cardinality.predicate}",),
                    f"{entity!r} of class {cardinality.class_name!r} has {count} "
                    f"{cardinality.predicate!r} facts; expected "
                    f"[{cardinality.min_count}, {cardinality.max_count if cardinality.max_count is not None else '...'}]"))
                if truncated:
                    return StructuralReport(tuple(violations), True, len(ordered))
        for datatype in sorted(suite.datatypes,
                                 key=lambda item: (item.class_name, item.predicate, item.kind)):
            if datatype.class_name not in classes:
                continue
            for fact in properties.get(datatype.predicate, ()):
                bad = [value for value in fact.values[1:]
                       if not (isinstance(value, Literal) and value.kind == datatype.kind)]
                if bad:
                    truncated = _emit(violations, max_violations, StructuralViolation(
                        "datatype-violation", (fact,),
                        (f"datatype:{datatype.class_name}:{datatype.predicate}:{datatype.kind}",),
                        f"{fact.predicate!r} on {entity!r} of class {datatype.class_name!r} "
                        f"holds a non-{datatype.kind} value"))
                    if truncated:
                        return StructuralReport(tuple(violations), True, len(ordered))
        for exclusive in sorted(suite.exclusive,
                                 key=lambda item: (item.class_name, item.predicates, item.mode)):
            if exclusive.class_name not in classes:
                continue
            present = [predicate for predicate in exclusive.predicates
                       if properties.get(predicate)]
            ok = (len(present) <= 1) if exclusive.mode == "at-most-one" else (len(present) == 1)
            if not ok:
                supporting = tuple(fact for predicate in present
                                   for fact in properties[predicate])
                supporting = supporting or (membership_fact[entity][exclusive.class_name],)
                truncated = _emit(violations, max_violations, StructuralViolation(
                    "exclusive-violation", supporting,
                    (f"exclusive:{exclusive.class_name}:{exclusive.mode}",),
                    f"{entity!r} of class {exclusive.class_name!r} violates "
                    f"{exclusive.mode} over {list(exclusive.predicates)!r}: "
                    f"present={present!r}"))
                if truncated:
                    return StructuralReport(tuple(violations), True, len(ordered))
    return StructuralReport(tuple(violations), truncated, len(ordered))
