"""Evidence-bearing validation over typed closures; never explosive, never inferring.

Validation reports conflicts against an explicit suite: denied positives,
disjoint class membership, reflexive loops on irreflexive properties and
functional-property conflicts. Violations carry the exact supporting facts and
constraint identities. The input closure stands unchanged: detection is
paraconsistent evidence, not classical inconsistency, and domain/range
entailment stays in the compiler. Equality-generating functionality belongs to
RE-11; this module only detects conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, ReasoningLimitError, _text, check_cancelled
from .terms import Literal
from .typed import TypedFact, _sort_key


@dataclass(frozen=True)
class Denial:
    """One explicit negative assertion: this ground proposition is denied."""

    predicate: str
    values: tuple[str | Literal, ...]

    def __post_init__(self) -> None:
        _text(self.predicate, "denial predicate")
        if not isinstance(self.values, (tuple, list)):
            raise ReasoningError("denial values must be a tuple or list")
        for value in self.values:
            if not isinstance(value, (str, Literal)) or (isinstance(value, str) and not value):
                raise ReasoningError("denial values must be entities or literals")
        object.__setattr__(self, "values", tuple(self.values))


@dataclass(frozen=True)
class ValidationSuite:
    """Closed-world validation scope: every check is explicit, nothing inferred.

    ``membership`` names the class-membership predicate the disjointness check
    reads (callers evaluating ontology closures pass the same predicate the
    compiler emits). Functional conflicts are detected, never resolved into
    equality; that generation is deferred.
    """

    membership: str
    denials: tuple[Denial, ...] = ()
    disjoint: tuple[tuple[str, str], ...] = ()
    irreflexive: tuple[str, ...] = ()
    functional: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.membership, "membership predicate")
        for name in ("denials", "disjoint", "irreflexive", "functional"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ReasoningError(f"suite {name} must be a tuple or list")
            object.__setattr__(self, name, tuple(value))
        if not all(isinstance(item, Denial) for item in self.denials):
            raise ReasoningError("denials must contain only Denial values")
        pairs = []
        for pair in self.disjoint:
            if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                    or not all(isinstance(name, str) and name for name in pair)):
                raise ReasoningError("disjoint pairs must be two non-empty class names")
            if pair[0] == pair[1]:
                raise ReasoningError("a class cannot be disjoint with itself")
            pairs.append(tuple(sorted(pair)))
        object.__setattr__(self, "disjoint", tuple(sorted(set(pairs))))
        for name in ("irreflexive", "functional"):
            for property_name in getattr(self, name):
                _text(property_name, f"suite {name} property")
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))

    @property
    def digest(self) -> str:
        """Structural identity of the validation scope."""
        import hashlib
        import json

        payload = ["gestaltdb-validation-v1", self.membership,
                   [[d.predicate, [_encoded(v) for v in d.values]] for d in self.denials],
                   [list(pair) for pair in self.disjoint],
                   list(self.irreflexive), list(self.functional)]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _encoded(value):
    if isinstance(value, Literal):
        return ["literal", value.kind, value.lexical]
    return ["entity", value]


@dataclass(frozen=True)
class Violation:
    """One bounded conflict with exact supporting facts and constraint source."""

    kind: str
    facts: tuple[TypedFact, ...]
    sources: tuple[str, ...]
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in ("denied-positive", "disjoint-members", "reflexive-loop", "functional-conflict"):
            raise ReasoningError(f"unknown violation kind: {self.kind!r}")
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
class ValidationReport:
    """Deterministic violation list; ``truncated`` marks a hit budget."""

    violations: tuple[Violation, ...]
    truncated: bool
    scanned_facts: int

    def __post_init__(self) -> None:
        if not isinstance(self.violations, (tuple, list)):
            raise ReasoningError("violations must be a tuple or list")
        if not all(isinstance(item, Violation) for item in self.violations):
            raise ReasoningError("violations must contain only Violation values")
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


def validate(facts: Iterable[TypedFact], suite: ValidationSuite, *,
             max_violations: int = 1000, cancel=None) -> ValidationReport:
    """Check a finite closure against the suite; never modifies or extends it.

    Facts stream in canonical order; violations are deterministic for identical
    inputs. ``max_violations`` bounds the report — hitting it sets
    ``truncated`` instead of failing. Cancellation raises without a report.

    Examples:
        Report a functional-property conflict without changing the facts::

            >>> from gestaltdb.reasoning.typed import TypedFact
            >>> from gestaltdb.reasoning.validation import ValidationSuite, validate
            >>> facts = [TypedFact("manager", ["alice", "bob"]), TypedFact("manager", ["alice", "carol"])]
            >>> report = validate(facts, ValidationSuite(membership="type", functional=["manager"]))
            >>> assert not report.clean
            >>> assert report.violations[0].kind == "functional-conflict"
            >>> assert len(facts) == 2

    Source labels identify generated constraints, not historical claim-version
    evidence. Input is buffered and sorted; ``max_violations`` limits the report
    rather than input memory. See the reasoning-validation guide for those bounds.
    """
    if not isinstance(suite, ValidationSuite):
        raise ReasoningError("suite must be a ValidationSuite")
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
    violations: list[Violation] = []
    truncated = False
    members: dict[object, set[str]] = {}
    for index, fact in enumerate(ordered):
        if index % 1024 == 0:
            check_cancelled(cancel)
        if fact.predicate == suite.membership and len(fact.values) == 2 and isinstance(fact.values[1], str):
            members.setdefault(fact.values[0], set()).add(fact.values[1])
    for denial in suite.denials:
        for fact in ordered:
            if fact.predicate == denial.predicate and fact.values == denial.values:
                truncated = _emit(violations, max_violations, Violation(
                    "denied-positive", (fact,),
                    (f"denial:{denial.predicate}",),
                    f"positive fact contradicts an explicit denial of {denial.predicate}"))
                break
        if truncated:
            return ValidationReport(tuple(violations), True, len(ordered))
    for left, right in suite.disjoint:
        for entity in sorted(members, key=lambda value: value if isinstance(value, str) else repr(value)):
            classes = members[entity]
            if left in classes and right in classes:
                supporting = tuple(fact for fact in ordered
                                   if fact.predicate == suite.membership and fact.values[0] == entity
                                   and fact.values[1] in (left, right))
                truncated = _emit(violations, max_violations, Violation(
                    "disjoint-members", supporting, (f"disjoint:{left}:{right}",),
                    f"{entity!r} is a member of disjoint classes {left!r} and {right!r}"))
                if truncated:
                    return ValidationReport(tuple(violations), True, len(ordered))
    for predicate in suite.irreflexive:
        for fact in ordered:
            if (fact.predicate == predicate and len(fact.values) == 2
                    and fact.values[0] == fact.values[1]):
                truncated = _emit(violations, max_violations, Violation(
                    "reflexive-loop", (fact,), (f"irreflexive:{predicate}",),
                    f"reflexive loop on irreflexive property {predicate!r}"))
                if truncated:
                    return ValidationReport(tuple(violations), True, len(ordered))
    for predicate in suite.functional:
        seen: dict[object, TypedFact] = {}
        for fact in ordered:
            if fact.predicate != predicate or not fact.values:
                continue
            subject, rest = fact.values[0], fact.values[1:]
            previous = seen.get(subject, None)
            if previous is None:
                seen[subject] = fact
            elif previous.values[1:] != rest:
                truncated = _emit(violations, max_violations, Violation(
                    "functional-conflict", (previous, fact), (f"functional:{predicate}",),
                    f"functional property {predicate!r} has conflicting values; equality is not inferred"))
                if truncated:
                    return ValidationReport(tuple(violations), True, len(ordered))
    return ValidationReport(tuple(violations), truncated, len(ordered))


def find_unsatisfiable(subclasses: Iterable[tuple[str, str]],
                       disjoint: Iterable[tuple[str, str]]) -> tuple[str, ...]:
    """Static schema check: classes forced into two disjoint superclasses.

    ``subclasses`` holds ``(sub, super)`` pairs; no instance data is read.
    A class is unsatisfiable when it reaches both sides of a disjoint pair
    through any-length chains, including itself. Returns sorted names.
    """
    try:
        edges = list(subclasses)
    except TypeError as exc:
        raise ReasoningError("subclasses must be iterable") from exc
    try:
        pairs = list(disjoint)
    except TypeError as exc:
        raise ReasoningError("disjoint must be iterable") from exc
    for edge in edges:
        if not isinstance(edge, (tuple, list)) or len(edge) != 2:
            raise ReasoningError("subclass relations must be (sub, super) pairs")
        sub, sup = edge
        _text(sub, "subclass"), _text(sup, "superclass")
    normalized = []
    for left, right in pairs:
        if (not isinstance(left, str) or not left or not isinstance(right, str) or not right):
            raise ReasoningError("disjoint pairs must be two non-empty class names")
        if left == right:
            raise ReasoningError("a class cannot be disjoint with itself")
        normalized.append((left, right))
    ancestors: dict[str, set[str]] = {}
    for sub, sup in edges:
        ancestors.setdefault(sub, set()).add(sub)
        ancestors.setdefault(sup, set()).add(sup)
    changed = True
    while changed:
        changed = False
        for sub, sup in edges:
            before = len(ancestors[sub])
            ancestors[sub] |= ancestors.get(sup, {sup})
            changed = changed or len(ancestors[sub]) > before
    bad = sorted({name for name, ups in ancestors.items()
                  for left, right in normalized if left in ups and right in ups})
    return tuple(bad)
