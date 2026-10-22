"""Complete time-range closure over pinned change segments.

Range evaluation never samples one instant: all distinct validity bounds
become cut points, the timeline splits into elementary segments with
uniform active inputs, and each segment evaluates independently under the
ordinary engine. A derived fact holds exactly on the union of segments
where it derives — the union of all support intersections — with exact
per-interval evidence. Point queries over the closure agree with
independent point evaluation by construction.

Equality is time-scoped: representatives rebuild per segment, so entities
equal during one interval never merge globally. Temporal negation,
aggregates and closed scopes fail closed; per-point stratified evaluation
is the caller's explicit fallback, never a silent mode switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .equality import CanonicalMapping, EqualityScope, canonicalize
from .intervals import Interval, IntervalSet
from .maintenance import _check_supported, _find_all_supports
from .model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from .typed import GenericEngine, GenericProgram, TypedFact, _sort_key, _TypedIndex


@dataclass(frozen=True)
class TemporalFact:
    """One base fact with an explicit non-empty validity range."""

    fact: TypedFact
    valid: IntervalSet

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        if not isinstance(self.valid, IntervalSet):
            raise ReasoningError("valid must be an IntervalSet")
        if not self.valid.intervals:
            raise ReasoningError("validity must be non-empty")


@dataclass(frozen=True)
class TemporalDerivation:
    """One exact support for a fact on one elementary segment."""

    fact: TypedFact
    segment: Interval
    rule_id: str
    premises: tuple[TypedFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        if not isinstance(self.segment, Interval):
            raise ReasoningError("segment must be an Interval")
        _text(self.rule_id, "rule_id")
        if not isinstance(self.premises, (tuple, list)) or not self.premises:
            raise ReasoningError("premises must be non-empty")
        if not all(isinstance(item, TypedFact) for item in self.premises):
            raise ReasoningError("premises must contain only TypedFact values")
        object.__setattr__(self, "premises", tuple(sorted(set(self.premises), key=_sort_key)))


@dataclass(frozen=True)
class TemporalClosure:
    """Range closure: segments, base validity and per-interval derivations."""

    segments: tuple[Interval, ...]
    base: tuple[TemporalFact, ...]
    derivations: tuple[TemporalDerivation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.segments, (tuple, list)) or not all(
                isinstance(item, Interval) for item in self.segments):
            raise ReasoningError("segments must contain only Interval values")
        object.__setattr__(self, "segments", tuple(sorted(set(self.segments),
                                                           key=lambda i: (i.start is not None, i.start,
                                                                          i.end is None, i.end))))
        if not isinstance(self.base, (tuple, list)) or not all(
                isinstance(item, TemporalFact) for item in self.base):
            raise ReasoningError("base must contain only TemporalFact values")
        object.__setattr__(self, "base", tuple(self.base))
        if not isinstance(self.derivations, (tuple, list)) or not all(
                isinstance(item, TemporalDerivation) for item in self.derivations):
            raise ReasoningError("derivations must contain only TemporalDerivation values")
        object.__setattr__(self, "derivations", tuple(self.derivations))

    def answers(self, fact: TypedFact) -> IntervalSet:
        """Exact validity of ``fact``: base ranges plus derived segments."""
        if not isinstance(fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        parts = [item.valid for item in self.base if item.fact == fact]
        parts.extend(IntervalSet([item.segment]) for item in self.derivations if item.fact == fact)
        out = IntervalSet([])
        for part in parts:
            out = out.union(part)
        return out

    def at(self, point: int) -> tuple[TypedFact, ...]:
        """Point query: every fact whose validity covers ``point``."""
        if isinstance(point, bool) or not isinstance(point, int):
            raise ReasoningError("point must be an integer microsecond")
        found = {item.fact for item in self.base if point in item.valid}
        found.update(item.fact for item in self.derivations if item.segment.contains(point))
        return tuple(sorted(found, key=_sort_key))

    def evidence(self, fact: TypedFact) -> tuple[TemporalDerivation, ...]:
        """Exact per-interval supports for one derived fact."""
        if not isinstance(fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        return tuple(item for item in self.derivations if item.fact == fact)


def change_segments(facts: Iterable[TemporalFact], *, max_segments: int = 10_000) -> tuple[Interval, ...]:
    """Split the covered timeline into elementary uniform-input segments."""
    if isinstance(max_segments, bool) or not isinstance(max_segments, int) or max_segments < 1:
        raise ReasoningError("max_segments must be a positive integer")
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(item, TemporalFact) for item in items):
        raise ReasoningError("facts must contain only TemporalFact values")
    cuts: set[int] = set()
    below = above = False
    for item in items:
        for interval in item.valid.intervals:
            if interval.start is None:
                below = True
            else:
                cuts.add(interval.start)
            if interval.end is None:
                above = True
            else:
                cuts.add(interval.end)
    ordered = sorted(cuts)
    segments: list[Interval] = []
    if below and ordered:
        segments.append(Interval(None, ordered[0]))
    for first, second in zip(ordered, ordered[1:]):
        if first < second:
            segments.append(Interval(first, second))
    if above and ordered:
        segments.append(Interval(ordered[-1], None))
    if not ordered and (below or above):
        segments.append(Interval(None, None))
    if len(segments) > max_segments:
        raise ReasoningLimitError(f"range evaluation exceeded max_segments={max_segments}")
    return tuple(segments)


@dataclass(frozen=True)
class RangeUpdate:
    """Validity-change outcome with reuse diagnostics and a rebound closure."""

    closure: TemporalClosure
    reused_segments: int
    recomputed_segments: int
    added: tuple[TypedFact, ...]
    removed: tuple[TypedFact, ...]
    work: int

    def __post_init__(self) -> None:
        if not isinstance(self.closure, TemporalClosure):
            raise ReasoningError("closure must be a TemporalClosure")
        for name in ("reused_segments", "recomputed_segments", "work"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReasoningError(f"{name} must be a non-negative integer")
        if not isinstance(self.added, (tuple, list)) or not all(isinstance(f, TypedFact) for f in self.added):
            raise ReasoningError("added must contain only TypedFact values")
        if not isinstance(self.removed, (tuple, list)) or not all(isinstance(f, TypedFact) for f in self.removed):
            raise ReasoningError("removed must contain only TypedFact values")
        object.__setattr__(self, "added", tuple(sorted(set(self.added), key=_sort_key)))
        object.__setattr__(self, "removed", tuple(sorted(set(self.removed), key=_sort_key)))


def _sample(segment: Interval) -> int:
    if segment.start is not None:
        return segment.start
    if segment.end is not None:
        return segment.end - 1
    return 0


def update_range(program: GenericProgram, closure: TemporalClosure, new_base: Iterable[TemporalFact], *,
                 limits: ReasoningLimits, max_segments: int = 10_000,
                 max_supports: int = 100_000, incremental: bool = True,
                 cancel: Callable[[], bool] | None = None) -> RangeUpdate:
    """Propagate changed validity ranges with segment memoization.

    Segments whose active base set is unchanged reuse the previous
    derivations verbatim; only changed segments re-evaluate. Removals are
    support-aware: a derived fact disappears only when its last supporting
    segment goes, and every removal traces to changed premises. With
    ``incremental=False`` — or whenever the previous generation is stale —
    the update falls back to a full ``evaluate_range`` instead of degrading.
    Point/range parity holds by construction: reused and recomputed segments
    use the same engine over the same active inputs.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(closure, TemporalClosure):
        raise ReasoningError("closure must be a TemporalClosure")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if not isinstance(incremental, bool):
        raise ReasoningError("incremental must be a bool")
    _check_supported(program)
    check_cancelled(cancel)
    try:
        items = list(new_base)
    except TypeError as exc:
        raise ReasoningError("new_base must be iterable") from exc
    if not all(isinstance(item, TemporalFact) for item in items):
        raise ReasoningError("new_base must contain only TemporalFact values")
    if not incremental:
        fresh = evaluate_range(program, items, limits=limits, max_segments=max_segments,
                               max_supports=max_supports, cancel=cancel)
        old_all = {item.fact for item in closure.base} | {item.fact for item in closure.derivations}
        new_all = {item.fact for item in fresh.base} | {item.fact for item in fresh.derivations}
        return RangeUpdate(fresh, 0, len(fresh.segments),
                           tuple(new_all - old_all), tuple(old_all - new_all), 0)
    segments = change_segments(items, max_segments=max_segments)
    old_by_active: dict[frozenset, list[TemporalDerivation]] = {}
    for derivation in closure.derivations:
        sample = _sample(derivation.segment)
        active = frozenset(item.fact for item in closure.base if sample in item.valid)
        old_by_active.setdefault(active, []).append(derivation)
    derivations: list[TemporalDerivation] = []
    seen: set[tuple] = set()
    work = [0]
    reused = recomputed = 0
    for segment in segments:
        check_cancelled(cancel)
        point = _sample(segment)
        active = [item.fact for item in items if point in item.valid]
        key = frozenset(active)
        if key in old_by_active:
            for derivation in old_by_active[key]:
                entry = (derivation.fact, segment, derivation.rule_id, derivation.premises)
                if entry not in seen:
                    seen.add(entry)
                    derivations.append(TemporalDerivation(*entry))
            reused += 1
            continue
        recomputed += 1
        result = GenericEngine().evaluate(program, active, limits=limits, cancel=cancel)
        fresh = set(result.facts) - set(active)
        if not fresh:
            continue
        full = _TypedIndex(sorted(set(result.facts), key=_sort_key))
        supports: list = []
        for target in sorted(fresh, key=_sort_key):
            _find_all_supports(program, full, target, limits=limits, max_supports=max_supports,
                                work=work, seen=set(), supports=supports, cancel=cancel)
        for support in supports:
            entry = (support.fact, segment, support.rule_id, support.premises)
            if entry not in seen:
                seen.add(entry)
                derivations.append(TemporalDerivation(*entry))
                if len(derivations) > max_supports:
                    raise ReasoningLimitError("range update exceeded max_supports")
    fresh_closure = TemporalClosure(segments, tuple(items), tuple(derivations))
    old_all = {item.fact for item in closure.base} | {item.fact for item in closure.derivations}
    new_all = {item.fact for item in fresh_closure.base} | {item.fact for item in fresh_closure.derivations}
    return RangeUpdate(fresh_closure, reused, recomputed,
                       tuple(new_all - old_all), tuple(old_all - new_all), work[0])


def evaluate_range(program: GenericProgram, facts: Iterable[TemporalFact], *,
                   limits: ReasoningLimits, max_segments: int = 10_000,
                   max_supports: int = 100_000,
                   cancel: Callable[[], bool] | None = None) -> TemporalClosure:
    """Evaluate every change segment; union per-segment closures with evidence.

    Temporal negation, aggregates and closed scopes fail closed: range
    evaluation covers the positive fragment only, and per-point stratified
    evaluation is the documented explicit fallback.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if isinstance(max_supports, bool) or not isinstance(max_supports, int) or max_supports < 1:
        raise ReasoningError("max_supports must be a positive integer")
    _check_supported(program)
    check_cancelled(cancel)
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(item, TemporalFact) for item in items):
        raise ReasoningError("facts must contain only TemporalFact values")
    segments = change_segments(items, max_segments=max_segments)
    derivations: list[TemporalDerivation] = []
    seen: set[tuple] = set()
    work = [0]
    for segment in segments:
        check_cancelled(cancel)
        point = _sample(segment)
        active = [item.fact for item in items if point in item.valid]
        result = GenericEngine().evaluate(program, active, limits=limits, cancel=cancel)
        fresh = set(result.facts) - set(active)
        if not fresh:
            continue
        full = _TypedIndex(sorted(set(result.facts), key=_sort_key))
        supports: list = []
        for target in sorted(fresh, key=_sort_key):
            _find_all_supports(program, full, target, limits=limits, max_supports=max_supports,
                                work=work, seen=set(), supports=supports, cancel=cancel)
        for support in supports:
            key = (support.fact, segment, support.rule_id, support.premises)
            if key not in seen:
                seen.add(key)
                derivations.append(TemporalDerivation(
                    support.fact, segment, support.rule_id, support.premises))
                if len(derivations) > max_supports:
                    raise ReasoningLimitError("range evaluation exceeded max_supports")
    return TemporalClosure(segments, tuple(items), tuple(derivations))


def canonicalize_segments(active_per_segment: Iterable[Iterable[TypedFact]], links=(), *,
                          scope_prefix: str, max_entities: int = 100_000,
                          cancel=None) -> tuple[CanonicalMapping, ...]:
    """Rebuild canonical representatives at every change point.

    Each segment canonicalizes under its own scope, so entities equal during
    one interval never merge globally. Time-varying links are separate
    evaluations per segment, never one global union.
    """
    _text(scope_prefix, "scope_prefix")
    if isinstance(max_entities, bool) or not isinstance(max_entities, int) or max_entities < 1:
        raise ReasoningError("max_entities must be a positive integer")
    try:
        groups = [list(group) for group in active_per_segment]
    except TypeError as exc:
        raise ReasoningError("active_per_segment must be iterable") from exc
    out = []
    for position, facts in enumerate(groups):
        check_cancelled(cancel)
        out.append(canonicalize(facts, links, scope=EqualityScope(f"{scope_prefix}@{position}"),
                                max_entities=max_entities, cancel=cancel))
    return tuple(out)
