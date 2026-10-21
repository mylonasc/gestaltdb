"""Bounded maintenance provenance: supports, reverse dependencies, insertion propagation.

A ``Support`` is one finite independent justification: a derived fact produced
by one rule from an exact premise tuple. It is not a witness DAG and never
enumerates recursive paths. ``DependencyIndex`` maps every known fact to its
alternative supports (preserving all sources with exact rule/premise versions)
and maintains the reverse premise-to-derived map without path enumeration.

``propagate_insertions`` extends a materialized closure by changed predicates
only, with support/output/work budgets and duplicate handling. Deletion and
rule-change invalidation belong to RE-16; here incomplete metadata fails
closed and demands explicit rematerialization.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable

from .model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from .typed import (
    GenericProgram,
    GenericRule,
    TypedFact,
    _digest_facts,
    _ready,
    _sort_key,
    _TypedIndex,
    _unify,
)


@dataclass(frozen=True)
class Support:
    """One independent derivation: rule plus exact premise facts."""

    fact: TypedFact
    rule_id: str
    premises: tuple[TypedFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("support fact must be a TypedFact")
        _text(self.rule_id, "rule_id")
        if not isinstance(self.premises, (tuple, list)) or not self.premises:
            raise ReasoningError("premises must be non-empty")
        if not all(isinstance(item, TypedFact) for item in self.premises):
            raise ReasoningError("premises must contain only TypedFact values")
        object.__setattr__(self, "premises", tuple(sorted(set(self.premises), key=_sort_key)))

    @property
    def digest(self) -> str:
        import hashlib
        import json

        from .typed import _encoded_term

        payload = ["gestaltdb-support-v1", self.rule_id,
                   [self.fact.predicate, [_encoded_term(v) for v in self.fact.values]],
                   sorted([item.predicate, [_encoded_term(v) for v in item.values]] for item in self.premises)]
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DependencyIndex:
    """Forward supports plus reverse premise map for one pinned generation.

    ``program_digest`` and ``horizon`` bind the exact ruleset and input facts
    the index was built from; any mismatch invalidates the generation and
    demands explicit rematerialization instead of incremental repair.
    """

    program_digest: str
    horizon: str
    supports: tuple[Support, ...]

    def __post_init__(self) -> None:
        _text(self.program_digest, "program_digest")
        _text(self.horizon, "horizon")
        if not isinstance(self.supports, (tuple, list)):
            raise ReasoningError("supports must be a tuple or list")
        if not all(isinstance(item, Support) for item in self.supports):
            raise ReasoningError("supports must contain only Support values")
        object.__setattr__(self, "supports", tuple(sorted(set(self.supports),
                                                           key=lambda s: (s.fact.predicate, s.rule_id,
                                                                          _sort_key(s.fact)))))

    @property
    def digest(self) -> str:
        import hashlib
        import json

        return "sha256:" + hashlib.sha256(json.dumps(
            ["gestaltdb-dependency-index-v1", self.program_digest, self.horizon,
             sorted(item.digest for item in self.supports)],
            ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def supports_for(self, fact: TypedFact) -> tuple[Support, ...]:
        return tuple(item for item in self.supports if item.fact == fact)

    def derived_from(self, premise: TypedFact) -> tuple[TypedFact, ...]:
        return tuple(sorted({item.fact for item in self.supports if premise in item.premises},
                            key=_sort_key))

    def premises_of(self, fact: TypedFact) -> tuple[TypedFact, ...]:
        out = set()
        for item in self.supports:
            if item.fact == fact:
                out.update(item.premises)
        return tuple(sorted(out, key=_sort_key))


@dataclass(frozen=True)
class PropagationResult:
    """Insertion-propagation outcome with preserved alternative supports."""

    facts: tuple[TypedFact, ...]
    added: tuple[TypedFact, ...]
    supports: tuple[Support, ...]
    index: DependencyIndex
    work: int

    def __post_init__(self) -> None:
        if not isinstance(self.facts, (tuple, list)) or not all(isinstance(f, TypedFact) for f in self.facts):
            raise ReasoningError("facts must contain only TypedFact values")
        if not isinstance(self.added, (tuple, list)) or not all(isinstance(f, TypedFact) for f in self.added):
            raise ReasoningError("added must contain only TypedFact values")
        if not isinstance(self.supports, (tuple, list)) or not all(isinstance(s, Support) for s in self.supports):
            raise ReasoningError("supports must contain only Support values")
        if not isinstance(self.index, DependencyIndex):
            raise ReasoningError("index must be a DependencyIndex")
        if isinstance(self.work, bool) or not isinstance(self.work, int) or self.work < 0:
            raise ReasoningError("work must be a non-negative integer")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))
        object.__setattr__(self, "added", tuple(sorted(set(self.added), key=_sort_key)))
        object.__setattr__(self, "supports", tuple(self.supports))


def _check_supported(program: GenericProgram) -> None:
    for rule in program.rules:
        if rule.negated:
            raise ReasoningError("maintenance does not support negation; rematerialize explicitly")
        for atom in (*rule.body, rule.head):
            if not isinstance(atom.predicate, str):
                raise ReasoningError("predicate variables require explicit expansion")
    if program.aggregates:
        raise ReasoningError("maintenance does not support aggregates; rematerialize explicitly")
    if program.closed:
        raise ReasoningError("maintenance does not support closed scopes; rematerialize explicitly")


def build_index(program: GenericProgram, closure: Iterable[TypedFact], supports: Iterable[Support]) -> DependencyIndex:
    """Assemble a generation-bound index; fails closed on any inconsistency."""
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    _check_supported(program)
    try:
        facts = list(closure)
    except TypeError as exc:
        raise ReasoningError("closure must be iterable") from exc
    if not all(isinstance(fact, TypedFact) for fact in facts):
        raise ReasoningError("closure must contain only TypedFact values")
    try:
        items = list(supports)
    except TypeError as exc:
        raise ReasoningError("supports must be iterable") from exc
    if not all(isinstance(item, Support) for item in items):
        raise ReasoningError("supports must contain only Support values")
    known = set(facts)
    for item in items:
        if item.fact not in known:
            raise ReasoningError("supports must reference known closure facts")
        if any(premise not in known for premise in item.premises):
            raise ReasoningError("support premises must be known closure facts")
        if item.rule_id not in {rule.rule_id for rule in program.rules}:
            raise ReasoningError(f"unknown rule in support: {item.rule_id!r}")
    return DependencyIndex(program.digest, _digest_facts(sorted(known, key=_sort_key)), tuple(items))


def _affected_predicates(program: GenericProgram, changed: set[str]) -> set[str]:
    downstream: dict[str, set[str]] = defaultdict(set)
    for rule in program.rules:
        for atom in rule.body:
            downstream[atom.predicate].add(rule.head.predicate)
    affected = set(changed)
    queue = list(changed)
    while queue:
        current = queue.pop()
        for nxt in downstream.get(current, ()):
            if nxt not in affected:
                affected.add(nxt)
                queue.append(nxt)
    return affected


def propagate_insertions(program: GenericProgram, closure: Iterable[TypedFact], new_facts: Iterable[TypedFact], *,
                         limits: ReasoningLimits, index: DependencyIndex | None = None,
                         max_supports: int = 100_000, cancel: Callable[[], bool] | None = None) -> PropagationResult:
    """Extend ``closure`` by insertions touching changed predicates only.

    Supports are recorded per derivation with duplicates collapsed; every
    alternative source is preserved. Budgets bound new facts, stored supports
    and candidate checks. A generation mismatch on ``index`` fails closed and
    demands rematerialization instead of incremental repair.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if isinstance(max_supports, bool) or not isinstance(max_supports, int) or max_supports < 1:
        raise ReasoningError("max_supports must be a positive integer")
    if index is not None and not isinstance(index, DependencyIndex):
        raise ReasoningError("index must be a DependencyIndex or None")
    _check_supported(program)
    check_cancelled(cancel)
    try:
        base = list(closure)
    except TypeError as exc:
        raise ReasoningError("closure must be iterable") from exc
    try:
        inserted = list(new_facts)
    except TypeError as exc:
        raise ReasoningError("new_facts must be iterable") from exc
    if not all(isinstance(fact, TypedFact) for fact in base):
        raise ReasoningError("closure must contain only TypedFact values")
    if not all(isinstance(fact, TypedFact) for fact in inserted):
        raise ReasoningError("new_facts must contain only TypedFact values")
    known = set(base)
    horizon = _digest_facts(sorted(known, key=_sort_key))
    if index is not None and (index.program_digest != program.digest or index.horizon != horizon):
        raise ReasoningError("stale dependency index; rematerialize explicitly")
    fresh = [fact for fact in inserted if fact not in known]
    for fact in fresh:
        known.add(fact)
        if len(known) > limits.max_facts:
            raise ReasoningLimitError(f"propagation exceeded max_facts={limits.max_facts}")
    existing_supports = list(index.supports) if index is not None else []
    if len(existing_supports) > max_supports:
        raise ReasoningLimitError(f"propagation exceeded max_supports={max_supports}")
    changed = {fact.predicate for fact in fresh}
    affected = _affected_predicates(program, changed)
    relevant = [rule for rule in program.rules
                if rule.head.predicate in affected and
                any(atom.predicate in affected for atom in rule.body)]
    full = _TypedIndex(sorted(known, key=_sort_key))
    by_predicate: dict[str, list[tuple[GenericRule, int]]] = defaultdict(list)
    for rule in relevant:
        seen: set = set()
        for position, atom in enumerate(rule.body):
            if atom not in seen:
                by_predicate[atom.predicate].append((rule, position))
                seen.add(atom)
    supports: list[Support] = list(existing_supports)
    seen_supports = {(item.fact, item.rule_id, item.premises) for item in supports}
    added: set[TypedFact] = set(fresh)
    delta = _TypedIndex(sorted(fresh, key=_sort_key))
    work = 0
    rounds = 0
    while delta.facts:
        check_cancelled(cancel)
        if rounds >= limits.max_iterations:
            raise ReasoningLimitError("propagation exceeded max_iterations")
        active = [(rule, position) for predicate in sorted(delta.by_predicate)
                  for rule, position in by_predicate.get(predicate, ())]
        if not active:
            break
        rounds += 1
        check_cancelled(cancel)
        active = [(rule, position) for predicate in sorted(delta.by_predicate)
                  for rule, position in by_predicate.get(predicate, ())]
        if not active:
            break
        derived: set[TypedFact] = set()
        for rule, pivot in active:
            for bindings, premises in _match_with_premises(rule, pivot, full, delta):
                work += 1
                if work > limits.max_candidate_rows:
                    raise ReasoningLimitError("propagation exceeded max_candidate_rows")
                values = tuple(bindings[term] if term in bindings else term for term in rule.head.args)
                conclusion = TypedFact(rule.head.predicate, values)
                key = (conclusion, rule.rule_id, tuple(sorted(premises, key=_sort_key)))
                if key not in seen_supports:
                    seen_supports.add(key)
                    supports.append(Support(conclusion, rule.rule_id, tuple(sorted(premises, key=_sort_key))))
                    if len(supports) > max_supports:
                        raise ReasoningLimitError(f"propagation exceeded max_supports={max_supports}")
                if conclusion not in known and conclusion not in derived:
                    if len(known) + len(derived) >= limits.max_facts:
                        raise ReasoningLimitError(f"propagation exceeded max_facts={limits.max_facts}")
                    derived.add(conclusion)
        if not derived:
            break
        ordered = sorted(derived, key=_sort_key)
        for fact in ordered:
            full.add(fact)
            known.add(fact)
            added.add(fact)
        delta = _TypedIndex(ordered)
    result_index = DependencyIndex(program.digest, _digest_facts(sorted(known, key=_sort_key)), tuple(supports))
    return PropagationResult(tuple(known), tuple(added), tuple(supports), result_index, work)


def _match_with_premises(rule, pivot, full, delta):
    remaining = tuple(index for index in range(len(rule.body)) if index != pivot)
    stack = [(dict(), dict(), remaining, pivot, iter(delta.candidates(rule.body[pivot], dict())))]
    while stack:
        bindings, premises, remaining, position, candidates = stack[-1]
        try:
            fact = next(candidates)
        except StopIteration:
            stack.pop()
            continue
        updated = _unify(rule.body[position], fact, bindings)
        if updated is None or not _ready(rule.filters, updated):
            continue
        used = dict(premises)
        used[position] = fact
        if not remaining:
            yield updated, tuple(used[index] for index in sorted(used))
            continue
        chosen, bucket = remaining[0], full.candidates(rule.body[remaining[0]], updated)
        for other in remaining[1:]:
            other_bucket = full.candidates(rule.body[other], updated)
            if len(other_bucket) < len(bucket):
                chosen, bucket = other, other_bucket
            if not bucket:
                break
        if bucket:
            stack.append((updated, used, tuple(index for index in remaining if index != chosen),
                          chosen, iter(bucket)))
