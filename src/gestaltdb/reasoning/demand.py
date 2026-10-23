"""Goal-directed demand evaluation via magic-set specialization; no solver.

A bound goal drives top-down adornment over the positive relational fragment:
per-occurrence body order forms the sideways information passing strategy,
magic predicates guard rule firing, and the rewritten program is evaluated
with the ordinary generic engine. Answers carry full-closure parity by
construction on the supported fragment; anything outside it fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, ReasoningLimitError, ReasoningLimits, Variable, _text, check_cancelled
from .terms import Literal
from .typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact, _sort_key


@dataclass(frozen=True)
class BoundGoal:
    """One bound query: constant predicate, mixed constant/variable args."""

    predicate: str
    args: tuple[str | Variable | Literal, ...]

    def __post_init__(self) -> None:
        _text(self.predicate, "goal predicate")
        if not isinstance(self.args, (tuple, list)) or not self.args:
            raise ReasoningError("goal args must be a non-empty tuple or list")
        for term in self.args:
            if isinstance(term, (Variable, Literal)):
                continue
            _text(term, "goal term")
        object.__setattr__(self, "args", tuple(self.args))

    @property
    def adornment(self) -> str:
        return "".join("f" if isinstance(term, Variable) else "b" for term in self.args)

    def _encoded(self) -> list:
        from .typed import _encoded_term
        return [self.predicate, [_encoded_term(term) for term in self.args]]


def _check_supported(program: GenericProgram) -> None:
    for rule in program.rules:
        if rule.filters:
            continue
        if rule.negated:
            raise ReasoningError("demand specialization does not support negation; use full closure")
    for rule in program.rules:
        for atom in (*rule.body, rule.head):
            if isinstance(atom.predicate, Variable):
                raise ReasoningError("predicate variables require explicit expansion")
    if program.aggregates:
        raise ReasoningError("demand specialization does not support aggregates; use full closure")
    if program.closed:
        raise ReasoningError("demand specialization does not support closed scopes; use full closure")


def _bound_positions(atom: TypedAtom, bound: set[Variable]) -> tuple[Variable | None, ...]:
    return tuple(term if isinstance(term, Variable) and term in bound else None for term in atom.args)


def required_predicates(program: GenericProgram, goal: BoundGoal) -> tuple[str, ...]:
    """Conservative input predicate plan for positive goal evaluation.

    Include headed predicates: they may also have asserted base facts. This
    does not restrict subjects or objects to the goal seed; recursive frontier
    bindings require a separate storage-aware execution plan.
    """
    if not isinstance(program, GenericProgram) or not isinstance(goal, BoundGoal):
        raise ReasoningError("GenericProgram and BoundGoal required")
    _check_supported(program)
    by_head: dict[str, set[str]] = {}
    for rule in program.rules:
        by_head.setdefault(rule.head.predicate, set()).update(atom.predicate for atom in rule.body)
    reachable, pending = {goal.predicate}, [goal.predicate]
    while pending:
        for predicate in sorted(by_head.get(pending.pop(), ())):
            if predicate not in reachable:
                reachable.add(predicate)
                pending.append(predicate)
    return tuple(sorted(reachable))


def specialize(program: GenericProgram, goal: BoundGoal) -> GenericProgram:
    """Rewrite to a magic-guarded program with full-closure answer parity.

    Deterministic: identical inputs always produce identical rule IDs, magic
    predicate names and rule order. Rules unreachable from the goal under any
    adornment are dropped — they cannot contribute to the answer.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(goal, BoundGoal):
        raise ReasoningError("goal must be a BoundGoal")
    _check_supported(program)
    arities: dict[str, set[int]] = {}
    for rule in program.rules:
        for atom in (*rule.body, rule.head):
            if isinstance(atom.predicate, str):
                arities.setdefault(atom.predicate, set()).add(len(atom.args))
    inconsistent = {predicate: sorted(sizes) for predicate, sizes in arities.items() if len(sizes) > 1}
    if inconsistent:
        raise ReasoningError(f"predicates must have consistent arity: {inconsistent}")
    if goal.predicate in arities and len(goal.args) not in arities[goal.predicate]:
        raise ReasoningError("goal arity does not match the program")
    by_head: dict[str, list[GenericRule]] = {}
    for rule in program.rules:
        by_head.setdefault(rule.head.predicate, []).append(rule)
    adorned: dict[str, set[str]] = {goal.predicate: {goal.adornment}}
    changed = True
    while changed:
        changed = False
        for predicate in sorted(adorned):
            for adornment in sorted(adorned[predicate]):
                for rule in by_head.get(predicate, []):
                    bound = {term for term, flag in zip(rule.head.args, adornment)
                             if flag == "b" and isinstance(term, Variable)}
                    for atom in rule.body:
                        if not isinstance(atom.predicate, str):
                            continue
                        key = "".join("b" if (isinstance(term, Variable) and term in bound)
                                      or not isinstance(term, Variable) else "f"
                                      for term in atom.args)
                        if key not in adorned.setdefault(atom.predicate, set()):
                            adorned[atom.predicate].add(key)
                            changed = True
                    bound |= {term for atom in rule.body for term in atom.args if isinstance(term, Variable)}
    magic_rules: list[GenericRule] = []
    rewritten: list[GenericRule] = []
    emitted: set[tuple] = set()
    for predicate in sorted(adorned):
        for adornment in sorted(adorned[predicate]):
            for rule in by_head.get(predicate, []):
                if (rule.rule_id, adornment) in emitted:
                    continue
                emitted.add((rule.rule_id, adornment))
                head_bound = [term for term, flag in zip(rule.head.args, adornment) if flag == "b"]
                guard = TypedAtom(f"magic#{predicate}#{adornment}", head_bound)
                rewritten.append(GenericRule(f"{rule.rule_id}#demand#{adornment}",
                                             (guard, *rule.body), rule.filters, rule.head))
                bound = {term for term, flag in zip(rule.head.args, adornment)
                         if flag == "b" and isinstance(term, Variable)}
                for position, atom in enumerate(rule.body):
                    if not isinstance(atom.predicate, str):
                        continue
                    key = "".join("b" if (isinstance(term, Variable) and term in bound) or not isinstance(term, Variable)
                                  else "f" for term in atom.args)
                    magic_head = TypedAtom(
                        f"magic#{atom.predicate}#{key}",
                        [term for term, bound_here in zip(atom.args, key) if bound_here == "b"])
                    prefix = [guard]
                    for earlier in rule.body[:position]:
                        if isinstance(earlier.predicate, str):
                            prefix.append(earlier)
                    magic_rules.append(GenericRule(
                        f"{rule.rule_id}#magic#{adornment}#{position}#{key}", tuple(prefix), (), magic_head))
                    bound |= {term for term in atom.args if isinstance(term, Variable)}
    seed_values = tuple(term for term, flag in zip(goal.args, goal.adornment) if flag == "b")
    return GenericProgram([*rewritten, *magic_rules])


@dataclass(frozen=True)
class DemandAnswer:
    """Goal-matching facts plus the specialized program that derived them."""

    facts: tuple[TypedFact, ...]
    program: GenericProgram
    iterations: int

    def __post_init__(self) -> None:
        if not isinstance(self.facts, (tuple, list)) or not all(isinstance(fact, TypedFact) for fact in self.facts):
            raise ReasoningError("facts must contain only TypedFact values")
        if not isinstance(self.program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise ReasoningError("iterations must be a non-negative integer")
        object.__setattr__(self, "facts", tuple(self.facts))


@dataclass(frozen=True)
class SpecializationCache:
    """Digest-keyed specialized programs; any identity change re-specializes."""

    entries: tuple[tuple[str, GenericProgram], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, (tuple, list)):
            raise ReasoningError("entries must be a tuple or list")
        for entry in self.entries:
            if (not isinstance(entry, (tuple, list)) or len(entry) != 2
                    or not isinstance(entry[0], str) or not entry[0]
                    or not isinstance(entry[1], GenericProgram)):
                raise ReasoningError("entries must be (digest, GenericProgram) pairs")
        object.__setattr__(self, "entries", tuple((key, program) for key, program in self.entries))

    def specialize(self, program: GenericProgram, goal: BoundGoal) -> GenericProgram:
        import hashlib
        import json

        if not isinstance(program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        if not isinstance(goal, BoundGoal):
            raise ReasoningError("goal must be a BoundGoal")
        key = "sha256:" + hashlib.sha256(json.dumps(
            [program.digest, goal._encoded()], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        for cached_key, cached in self.entries:
            if cached_key == key:
                return cached
        return specialize(program, goal)

    def remembering(self, program: GenericProgram, goal: BoundGoal) -> tuple[GenericProgram, "SpecializationCache"]:
        specialized = self.specialize(program, goal)
        import hashlib
        import json

        key = "sha256:" + hashlib.sha256(json.dumps(
            [program.digest, goal._encoded()], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        entries = tuple(entry for entry in self.entries if entry[0] != key) + ((key, specialized),)
        return specialized, SpecializationCache(entries)


def answer(program: GenericProgram, facts: Iterable[TypedFact], goal: BoundGoal, *,
           limits: ReasoningLimits, cache: SpecializationCache | None = None,
           cancel=None) -> DemandAnswer:
    """Evaluate the demand-specialized program and return goal-matching facts.

    Unsupported combinations (negation, aggregates, closed scopes, predicate
    variables) fail closed here instead of silently falling back — callers
    that need those modes use full closure explicitly and document it.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(goal, BoundGoal):
        raise ReasoningError("goal must be a BoundGoal")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if cache is not None and not isinstance(cache, SpecializationCache):
        raise ReasoningError("cache must be a SpecializationCache or None")
    check_cancelled(cancel)
    specialized = cache.specialize(program, goal) if cache is not None else specialize(program, goal)
    seed_values = tuple(term for term, flag in zip(goal.args, goal.adornment) if flag == "b")
    seed = TypedFact(f"magic#{goal.predicate}#{goal.adornment}",
                     [term for term in seed_values if isinstance(term, (str, Literal))])
    try:
        inputs = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(fact, TypedFact) for fact in inputs):
        raise ReasoningError("facts must contain only TypedFact values")
    result = GenericEngine().evaluate(specialized, [*inputs, seed], limits=limits, cancel=cancel)
    matched = []
    for fact in result.facts:
        if fact.predicate != goal.predicate or len(fact.values) != len(goal.args):
            continue
        bindings = {}
        ok = True
        for term, value in zip(goal.args, fact.values, strict=True):
            if isinstance(term, Variable):
                if term in bindings and bindings[term] != value:
                    ok = False
                    break
                bindings[term] = value
            elif isinstance(term, Literal):
                if not isinstance(value, Literal) or value != term:
                    ok = False
                    break
            elif term != value:
                ok = False
                break
        if ok:
            matched.append(fact)
    return DemandAnswer(tuple(sorted(set(matched), key=_sort_key)), specialized, result.iterations)
