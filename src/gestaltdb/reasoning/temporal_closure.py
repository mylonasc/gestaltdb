"""Complete time-range closure over pinned change segments.

Range evaluation never samples one instant: all distinct validity bounds
become cut points, the timeline splits into elementary segments with
uniform active inputs, and each segment evaluates independently under the
ordinary engine. A derived fact holds exactly on the union of segments
where it derives — the union of all support intersections — with exact
per-interval evidence. Point queries over the closure agree with
independent point evaluation by construction.

Equality is time-scoped: representatives rebuild per segment, so entities
equal during one interval never merge globally. Stratified negation over
declared closed scopes and nonrecursive aggregates evaluate per segment —
each segment runs ordinary point evaluation (stratified when needed) over
its uniform active inputs — with per-interval absence and aggregate
evidence. Per-point evaluation parity holds by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .equality import CanonicalMapping, EqualityScope, canonicalize
from .intervals import Interval, IntervalSet
from .maintenance import _find_all_supports
from .model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from .typed import GenericEngine, GenericProgram, TypedFact, _sort_key, _TypedIndex
from .typed_proofs import AbsenceCheck, AggregateDerivation


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
    """One exact support for a fact on one elementary segment.

    ``absence`` records successful closed-scope probes behind stratified
    derivations; ``aggregates`` carries the grouped operations with their
    contributing facts. Aggregate conclusions carry operands instead of
    positional premises.
    """

    fact: TypedFact
    segment: Interval
    rule_id: str
    premises: tuple[TypedFact, ...]
    absence: tuple[AbsenceCheck, ...] = ()
    aggregates: tuple[AggregateDerivation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        if not isinstance(self.segment, Interval):
            raise ReasoningError("segment must be an Interval")
        _text(self.rule_id, "rule_id")
        if not isinstance(self.premises, (tuple, list)):
            raise ReasoningError("premises must be a tuple or list")
        if not all(isinstance(item, TypedFact) for item in self.premises):
            raise ReasoningError("premises must contain only TypedFact values")
        object.__setattr__(self, "premises", tuple(sorted(set(self.premises), key=_sort_key)))
        if not isinstance(self.absence, (tuple, list)) or not all(
                isinstance(item, AbsenceCheck) for item in self.absence):
            raise ReasoningError("absence must contain only AbsenceCheck values")
        object.__setattr__(self, "absence", tuple(self.absence))
        if not isinstance(self.aggregates, (tuple, list)) or not all(
                isinstance(item, AggregateDerivation) for item in self.aggregates):
            raise ReasoningError("aggregates must contain only AggregateDerivation values")
        object.__setattr__(self, "aggregates", tuple(self.aggregates))
        if not self.premises and not self.aggregates:
            raise ReasoningError("premises must be non-empty without aggregate evidence")


@dataclass(frozen=True)
class TemporalClosure:
    """Range closure: segments, base validity and per-interval derivations."""

    segments: tuple[Interval, ...]
    base: tuple[TemporalFact, ...]
    derivations: tuple[TemporalDerivation, ...]
    program_digest: str | None = None

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
        if self.program_digest is not None:
            _text(self.program_digest, "program_digest")

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


@dataclass(frozen=True)
class ClosureVerification:
    """Independent re-verification counts for a pinned range closure."""

    segments: int
    derivations: int
    points_checked: int

    def __post_init__(self) -> None:
        for name in ("segments", "derivations", "points_checked"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReasoningError(f"{name} must be a non-negative integer")


def verify_closure(program: GenericProgram, closure: TemporalClosure, *,
                   limits: ReasoningLimits,
                   cancel: Callable[[], bool] | None = None) -> ClosureVerification:
    """Independently re-verify a range closure through a different path.

    Re-derives the segmentation from base validity, point-evaluates every
    segment fresh, and checks that every derived fact, derivation and
    validity answer matches. Any drift — stale segments, missing supports,
    wrong validity — fails closed. This is the conformance gate for
    published temporal/profile combinations: supported paths verify;
    anything else rematerializes.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(closure, TemporalClosure):
        raise ReasoningError("closure must be a TemporalClosure")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    check_cancelled(cancel)
    if closure.program_digest is not None and closure.program_digest != program.digest:
        raise ReasoningError("closure program identity drifted; rematerialize explicitly")
    expected_segments = change_segments(closure.base, max_segments=max(1, len(closure.segments)))
    if expected_segments != closure.segments:
        raise ReasoningError("closure segments drifted from base validity; rematerialize explicitly")
    rule_ids = {rule.rule_id for rule in (*program.rules, *program.aggregates)}
    use_stratified = bool(program.closed) or any(rule.negated for rule in program.rules)
    points = 0
    # One sample per segment suffices: segments tile maximal constant-active
    # intervals, so every point in a segment shares its active set and closure.
    for segment in closure.segments:
        check_cancelled(cancel)
        point = _sample(segment)
        active = [item.fact for item in closure.base if point in item.valid]
        if use_stratified:
            run = GenericEngine().evaluate_stratified(program, active, limits=limits, cancel=cancel)
            fresh = set(run.result.facts)
        else:
            fresh = set(GenericEngine().evaluate(program, active, limits=limits, cancel=cancel).facts)
        if set(closure.at(point)) != fresh:
            raise ReasoningError("point closure mismatch; rematerialize explicitly")
        points += 1
    for derivation in closure.derivations:
        if derivation.segment not in closure.segments:
            raise ReasoningError("derivation references an unknown segment")
        if derivation.rule_id not in rule_ids:
            raise ReasoningError(f"unknown rule in derivation: {derivation.rule_id!r}")
        covered = closure.at(_sample(derivation.segment))
        if derivation.fact not in covered:
            raise ReasoningError("derivation fact not covered by its own segment")
        if any(premise not in covered for premise in derivation.premises):
            raise ReasoningError("derivation premises not active on their segment")
        for probe in derivation.absence:
            if probe.scope != tuple(sorted(program.closed)):
                raise ReasoningError("absence scope drifted from the program declaration")
            if TypedFact(probe.predicate, probe.args) in covered:
                raise ReasoningError("absence probe is present on its segment")
        for item in derivation.aggregates:
            if item.rule_id != derivation.rule_id:
                raise ReasoningError("aggregate evidence references another rule")
            if any(operand not in covered for operand in item.operands):
                raise ReasoningError("aggregate operands not active on their segment")
    for item in closure.base:
        uncovered = item.valid.difference(closure.answers(item.fact))
        if uncovered.intervals:
            raise ReasoningError("base validity not covered by closure answers")
    return ClosureVerification(len(closure.segments), len(closure.derivations), points)


def _sample(segment: Interval) -> int:
    if segment.start is not None:
        return segment.start
    if segment.end is not None:
        return segment.end - 1
    return 0


def _range_needs_trace(program: GenericProgram) -> bool:
    """Trace-evidence path for negation, closed scopes and aggregates.

    Pure-positive programs keep the legacy all-supports evidence path
    byte-identical; everything else records first-witness evidence from
    proof traces. Both paths evaluate each segment as an independent point
    closure, so point/range parity holds by construction.
    """
    return (bool(program.closed) or bool(program.aggregates)
            or any(rule.negated for rule in program.rules))


def _segment_derivations(program: GenericProgram, active: list[TypedFact], segment: Interval, *,
                         limits: ReasoningLimits, max_supports: int, work: list,
                         cancel=None) -> list[TemporalDerivation]:
    """Per-segment closure with evidence; legacy or trace path by program shape."""
    use_stratified = bool(program.closed) or any(rule.negated for rule in program.rules)
    need_trace = _range_needs_trace(program)
    if use_stratified:
        run = GenericEngine().evaluate_stratified(program, active, limits=limits,
                                                  collect_proofs=True, cancel=cancel)
        result, trace = run.result, run.proofs
    elif need_trace:
        result = GenericEngine().evaluate(program, active, limits=limits,
                                          collect_proofs=True, cancel=cancel)
        trace = result.proofs
    else:
        result = GenericEngine().evaluate(program, active, limits=limits, cancel=cancel)
        trace = None
    fresh = set(result.facts) - set(active)
    if not fresh:
        return []
    if not need_trace:
        full = _TypedIndex(sorted(set(result.facts), key=_sort_key))
        supports: list = []
        for target in sorted(fresh, key=_sort_key):
            _find_all_supports(program, full, target, limits=limits, max_supports=max_supports,
                               work=work, seen=set(), supports=supports, cancel=cancel)
        return [TemporalDerivation(support.fact, segment, support.rule_id, support.premises)
                for support in supports]
    assert trace is not None
    derivations = []
    for node in trace.nodes:
        if node.rule is None or node.fact not in fresh:
            continue
        derivations.append(TemporalDerivation(node.fact, segment, node.rule.rule_id,
                                              node.premises, node.absence, node.aggregates))
    return derivations


def _scheduled_active(items: list, *, max_rows: int, max_events: int,
                      max_segments: int, max_memberships: int,
                      cancel=None) -> list[tuple[Interval, list]]:
    """Schedule active base facts per segment with one interval event sweep.

    Returns ``(segment, facts)`` pairs where ``facts`` holds one entry per
    active input row, preserving overlapping-assertion multiplicity exactly
    like a per-segment validity scan. Segments tile the same change points
    ``change_segments`` emits, so ``verify_closure`` keeps independently
    checking segment identity through its own scan path.
    """
    from .temporal_sweep import active_segments

    return [(scheduled.interval, [items[row].fact for row in scheduled.rows])
            for scheduled in active_segments(items, max_rows=max_rows, max_events=max_events,
                                             max_segments=max_segments,
                                             max_memberships=max_memberships, cancel=cancel)]


def update_range(program: GenericProgram, closure: TemporalClosure, new_base: Iterable[TemporalFact], *,
                 limits: ReasoningLimits, max_segments: int = 10_000,
                 max_supports: int = 100_000, incremental: bool = True,
                 max_sweep_rows: int = 100_000, max_sweep_events: int = 200_000,
                 max_sweep_memberships: int = 1_000_000,
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

    Active inputs on the new base are scheduled once with an interval event
    sweep (see ``evaluate_range``); segment memoization keys are unchanged.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(closure, TemporalClosure):
        raise ReasoningError("closure must be a TemporalClosure")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if not isinstance(incremental, bool):
        raise ReasoningError("incremental must be a bool")
    check_cancelled(cancel)
    try:
        items = list(new_base)
    except TypeError as exc:
        raise ReasoningError("new_base must be iterable") from exc
    if not all(isinstance(item, TemporalFact) for item in items):
        raise ReasoningError("new_base must contain only TemporalFact values")
    if not incremental or closure.program_digest != program.digest:
        fresh = evaluate_range(program, items, limits=limits, max_segments=max_segments,
                               max_supports=max_supports,
                               max_sweep_rows=max_sweep_rows, max_sweep_events=max_sweep_events,
                               max_sweep_memberships=max_sweep_memberships, cancel=cancel)
        old_all = {item.fact for item in closure.base} | {item.fact for item in closure.derivations}
        new_all = {item.fact for item in fresh.base} | {item.fact for item in fresh.derivations}
        return RangeUpdate(fresh, 0, len(fresh.segments),
                           tuple(new_all - old_all), tuple(old_all - new_all), 0)
    scheduled = _scheduled_active(items, max_rows=max_sweep_rows, max_events=max_sweep_events,
                                  max_segments=max_segments, max_memberships=max_sweep_memberships,
                                  cancel=cancel)
    segments = tuple(interval for interval, _ in scheduled)
    # Memo key is sound: segments tile maximal constant-active intervals, and
    # closure evaluation is a pure set function of (program, active facts).
    # Reused derivations are re-segmented to the new interval (entry carries
    # `segment`), so equal active sets share fact/rule/premise content only.
    old_by_active: dict[frozenset, list[TemporalDerivation]] = {}
    for derivation in closure.derivations:
        sample = _sample(derivation.segment)
        active = frozenset(item.fact for item in closure.base if sample in item.valid)
        old_by_active.setdefault(active, []).append(derivation)
    derivations: list[TemporalDerivation] = []
    seen: set[tuple] = set()
    work = [0]
    reused = recomputed = 0
    for segment, active in scheduled:
        check_cancelled(cancel)
        key = frozenset(active)
        if key in old_by_active:
            for derivation in old_by_active[key]:
                entry = (derivation.fact, segment, derivation.rule_id, derivation.premises,
                         derivation.absence, derivation.aggregates)
                if entry not in seen:
                    seen.add(entry)
                    derivations.append(TemporalDerivation(*entry))
            reused += 1
            continue
        recomputed += 1
        for derivation in _segment_derivations(program, active, segment, limits=limits,
                                              max_supports=max_supports, work=work, cancel=cancel):
            entry = (derivation.fact, derivation.segment, derivation.rule_id,
                     derivation.premises, derivation.absence, derivation.aggregates)
            if entry not in seen:
                seen.add(entry)
                derivations.append(derivation)
                if len(derivations) > max_supports:
                    raise ReasoningLimitError("range update exceeded max_supports")
    fresh_closure = TemporalClosure(segments, tuple(items), tuple(derivations), program.digest)
    old_all = {item.fact for item in closure.base} | {item.fact for item in closure.derivations}
    new_all = {item.fact for item in fresh_closure.base} | {item.fact for item in fresh_closure.derivations}
    return RangeUpdate(fresh_closure, reused, recomputed,
                       tuple(new_all - old_all), tuple(old_all - new_all), work[0])


def evaluate_range(program: GenericProgram, facts: Iterable[TemporalFact], *,
                   limits: ReasoningLimits, max_segments: int = 10_000,
                   max_supports: int = 100_000, max_sweep_rows: int = 100_000,
                   max_sweep_events: int = 200_000, max_sweep_memberships: int = 1_000_000,
                   cancel: Callable[[], bool] | None = None) -> TemporalClosure:
    """Evaluate every change segment; union per-segment closures with evidence.

    Each segment runs ordinary point evaluation over its uniform active
    inputs — stratified when the program uses negation or closed scopes —
    so stratified negation and nonrecursive aggregates evaluate with
    per-interval absence and aggregate evidence. Derivations record only
    what actually fired on their segment: gaps stay gaps, and empty groups
    infer no zero counts.

    Active inputs are scheduled once with an interval event sweep instead of
    scanning every input at every segment: each row contributes add/remove
    events at its half-open bounds (multiplicity preserved for overlapping
    assertions), and segments tile exactly the change points
    ``change_segments`` would emit. Per-segment evaluation, support
    discovery and evidence identity are unchanged.

    Examples:
        Derive eligibility only during the premise's half-open validity::

            >>> from gestaltdb.reasoning.model import ReasoningLimits, Variable
            >>> from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
            >>> from gestaltdb.reasoning.intervals import Interval, IntervalSet
            >>> from gestaltdb.reasoning.temporal_closure import TemporalFact, evaluate_range, verify_closure
            >>> x = Variable("x")
            >>> program = GenericProgram([GenericRule("r", [TypedAtom("member", [x])], [], TypedAtom("eligible", [x]))])
            >>> base = [TemporalFact(TypedFact("member", ["alice"]), IntervalSet([Interval(0, 10)]))]
            >>> closure = evaluate_range(program, base, limits=ReasoningLimits())
            >>> assert TypedFact("eligible", ["alice"]) in closure.at(5)
            >>> assert TypedFact("eligible", ["alice"]) not in closure.at(10)
            >>> assert verify_closure(program, closure, limits=ReasoningLimits()).points_checked == 1

    Times here are integer microseconds, not database commit IDs. Database-backed
    extraction and publication require the separate range adapters; see the
    reasoning-range-admission and reasoning-bound-range-publication guides.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if isinstance(max_supports, bool) or not isinstance(max_supports, int) or max_supports < 1:
        raise ReasoningError("max_supports must be a positive integer")
    check_cancelled(cancel)
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(item, TemporalFact) for item in items):
        raise ReasoningError("facts must contain only TemporalFact values")
    scheduled = _scheduled_active(items, max_rows=max_sweep_rows, max_events=max_sweep_events,
                                  max_segments=max_segments, max_memberships=max_sweep_memberships,
                                  cancel=cancel)
    segments = tuple(interval for interval, _ in scheduled)
    derivations: list[TemporalDerivation] = []
    seen: set[tuple] = set()
    work = [0]
    for segment, active in scheduled:
        check_cancelled(cancel)
        for derivation in _segment_derivations(program, active, segment, limits=limits,
                                              max_supports=max_supports, work=work, cancel=cancel):
            key = (derivation.fact, derivation.segment, derivation.rule_id,
                   derivation.premises, derivation.absence, derivation.aggregates)
            if key not in seen:
                seen.add(key)
                derivations.append(derivation)
                if len(derivations) > max_supports:
                    raise ReasoningLimitError("range evaluation exceeded max_supports")
    return TemporalClosure(segments, tuple(items), tuple(derivations), program.digest)


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
