"""Finite typed Datalog: n-ary relations over entities and strict literals.

Ground values are nonempty entity strings or ``Literal`` values. Rule atoms
carry entity/variable/literal terms; ``Comparison`` filters apply kind-strict
``== != < <= > >=`` after their variables bind. Predicate variables are
accepted by the model but rejected at evaluation until an explicit expansion
lands. NaN never matches, cross-kind filters never match, and every filter
variable must occur in a relational body atom.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable

from .model import (
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    Variable,
    _text,
    check_cancelled,
)
from .protocols import Evidence
from .terms import Literal, compare, equal


def _ground(value: object, name: str) -> str | Literal:
    if isinstance(value, Literal):
        return value
    return _text(value, name)


def _term(value: object, name: str) -> str | Variable | Literal:
    if isinstance(value, (Variable, Literal)):
        return value
    return _text(value, name)


@dataclass(frozen=True)
class TypedFact:
    """One ground n-ary assertion; values are entities or literals."""

    predicate: str
    values: tuple[str | Literal, ...]

    def __post_init__(self) -> None:
        _text(self.predicate, "fact predicate")
        if not isinstance(self.values, (tuple, list)):
            raise ReasoningError("fact values must be a tuple or list")
        values = tuple(_ground(value, "fact value") for value in self.values)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class TypedAtom:
    """Relational atom; predicate variables await explicit expansion."""

    predicate: str | Variable
    args: tuple[str | Variable | Literal, ...]

    def __post_init__(self) -> None:
        if isinstance(self.predicate, Variable):
            pass
        else:
            _text(self.predicate, "atom predicate")
        if not isinstance(self.args, (tuple, list)):
            raise ReasoningError("atom args must be a tuple or list")
        args = tuple(_term(value, "atom term") for value in self.args)
        object.__setattr__(self, "args", args)

    @property
    def variables(self) -> frozenset[Variable]:
        found = {term for term in self.args if isinstance(term, Variable)}
        if isinstance(self.predicate, Variable):
            found.add(self.predicate)
        return frozenset(found)


@dataclass(frozen=True)
class Comparison:
    """Kind-strict filter; incomparability fails the match, never errors."""

    op: str
    left: Variable | Literal
    right: Variable | Literal

    def __post_init__(self) -> None:
        if self.op not in ("==", "!=", "<", "<=", ">", ">="):
            raise ReasoningError(f"comparison op must be one of == != < <= > >=, got {self.op!r}")
        for name in ("left", "right"):
            value = getattr(self, name)
            if not isinstance(value, (Variable, Literal)):
                raise ReasoningError(f"comparison {name} must be a Variable or Literal")

    @property
    def variables(self) -> frozenset[Variable]:
        return frozenset(term for term in (self.left, self.right) if isinstance(term, Variable))

    def holds(self, bindings: dict[Variable, object]) -> bool:
        """Evaluate under complete bindings; unbound variables fail closed.

        Entity operands are incomparable with literals, so every operator —
        including ``==`` and ``!=`` — returns ``False`` rather than crossing
        the entity/literal sort boundary. Only unbound variables raise.
        """
        left = bindings.get(self.left, self.left) if isinstance(self.left, Variable) else self.left
        right = bindings.get(self.right, self.right) if isinstance(self.right, Variable) else self.right
        if isinstance(left, Variable) or isinstance(right, Variable):
            raise ReasoningError("comparison evaluated with unbound variables")
        if not isinstance(left, Literal) or not isinstance(right, Literal):
            return False
        if self.op == "==":
            return equal(left, right)
        if self.op == "!=":
            return not equal(left, right)
        order = compare(left, right)
        if order is None:
            return False
        return { "<": order < 0, "<=": order <= 0, ">": order > 0, ">=": order >= 0}[self.op]


@dataclass(frozen=True)
class GenericRule:
    """Safe positive rule with optional filters; heads stay single and relational."""

    rule_id: str
    body: tuple[TypedAtom, ...]
    filters: tuple[Comparison, ...]
    head: TypedAtom

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        for name in ("body", "filters"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ReasoningError(f"rule {name} must be a tuple or list")
        body = tuple(self.body)
        filters = tuple(self.filters)
        if not body or not all(isinstance(atom, TypedAtom) for atom in body):
            raise ReasoningError("rule body must contain at least one TypedAtom")
        if not all(isinstance(item, Comparison) for item in filters):
            raise ReasoningError("rule filters must contain only Comparison values")
        if not isinstance(self.head, TypedAtom):
            raise ReasoningError("rule head must be a TypedAtom")
        if isinstance(self.head.predicate, Variable):
            raise ReasoningError("head predicates must be constant")
        for atom in body:
            if isinstance(atom.predicate, Variable):
                raise ReasoningError("predicate variables require explicit expansion")
        bound = frozenset().union(*(atom.variables for atom in body))
        for item in filters:
            if not item.variables <= bound:
                raise ReasoningError("filter variables must occur in the relational body")
        head_variables = {term for term in self.head.args if isinstance(term, Variable)}
        if not head_variables <= bound:
            raise ReasoningError("unsafe rule: head variables must occur in the body")
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "filters", filters)


def _encoded_term(term):
    if isinstance(term, Variable):
        return ["variable", term.name]
    if isinstance(term, Literal):
        return ["literal", term.kind, term.lexical]
    return ["entity", term]


@dataclass(frozen=True)
class GenericProgram:
    """Validated rule catalog, canonically ordered by unique rule ID."""

    rules: tuple[GenericRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.rules, (tuple, list)):
            raise ReasoningError("program rules must be a tuple or list")
        rules = tuple(self.rules)
        if not all(isinstance(rule, GenericRule) for rule in rules):
            raise ReasoningError("program rules must contain only GenericRule values")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ReasoningError("program rule IDs must be unique")
        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda rule: rule.rule_id)))

    @property
    def digest(self) -> str:
        """Versioned structural identity; variable names and positions participate."""
        import hashlib
        import json

        payload = ["gestaltdb-generic-v1", [
            [rule.rule_id,
             [[atom.predicate if isinstance(atom.predicate, str) else ["variable", atom.predicate.name],
               [_encoded_term(term) for term in atom.args]] for atom in rule.body],
             [[item.op, _encoded_term(item.left), _encoded_term(item.right)] for item in rule.filters],
             [rule.head.predicate, [_encoded_term(term) for term in rule.head.args]]]
            for rule in self.rules]]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sort_key(fact: TypedFact):
    def value_key(value):
        if isinstance(value, Literal):
            return (1, value.kind, value.lexical)
        return (0, "", value)

    return (fact.predicate, tuple(value_key(value) for value in fact.values))


@dataclass(frozen=True)
class GenericResult:
    """Complete closure in canonical order; evaluators never return partial runs."""

    facts: tuple[TypedFact, ...]
    iterations: int
    program_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.facts, (tuple, list)):
            raise ReasoningError("result facts must be a tuple or list")
        if not all(isinstance(fact, TypedFact) for fact in self.facts):
            raise ReasoningError("result facts must contain only TypedFact values")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise ReasoningError("iterations must be a non-negative integer")
        _text(self.program_digest, "program_digest")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))

    def __contains__(self, fact: object) -> bool:
        return isinstance(fact, TypedFact) and fact in set(self.facts)


class _TypedIndex:
    def __init__(self, facts: Iterable[TypedFact] = ()) -> None:
        self.facts: set[TypedFact] = set()
        self.by_predicate: dict[str, list[TypedFact]] = defaultdict(list)
        self.by_position: dict[tuple[str, int, object], list[TypedFact]] = defaultdict(list)
        for fact in facts:
            self.add(fact)

    @staticmethod
    def _key(value):
        return ("literal", value.kind, value.lexical) if isinstance(value, Literal) else ("entity", value)

    def add(self, fact: TypedFact) -> None:
        if fact in self.facts:
            return
        self.facts.add(fact)
        self.by_predicate[fact.predicate].append(fact)
        for position, value in enumerate(fact.values):
            self.by_position[fact.predicate, position, self._key(value)].append(fact)

    def candidates(self, atom: TypedAtom, bindings: dict[Variable, object]) -> tuple[TypedFact, ...]:
        ground = [bindings.get(term, term) if isinstance(term, Variable) else term for term in atom.args]
        if all(not isinstance(value, Variable) for value in ground):
            exact = TypedFact(atom.predicate, tuple(ground))
            return (exact,) if exact in self.facts else ()
        buckets = [self.by_position[atom.predicate, position, self._key(value)]
                   for position, value in enumerate(ground) if not isinstance(value, Variable)]
        if not buckets:
            return tuple(self.by_predicate.get(atom.predicate, ()))
        return tuple(min(buckets, key=len))


def _unify(atom: TypedAtom, fact: TypedFact, bindings: dict) -> dict | None:
    if atom.predicate != fact.predicate or len(atom.args) != len(fact.values):
        return None
    updated = dict(bindings)
    for term, value in zip(atom.args, fact.values):
        if isinstance(term, Variable):
            previous = updated.get(term)
            if previous is not None and previous != value:
                return None
            updated[term] = value
        elif isinstance(term, Literal):
            if not isinstance(value, Literal) or not equal(term, value):
                return None
        elif term != value:
            return None
    return updated


class _TypedBudget:
    def __init__(self, limits: ReasoningLimits) -> None:
        self.limits = limits
        self.candidate_rows = 0

    def candidate(self) -> None:
        self.candidate_rows += 1
        if self.candidate_rows > self.limits.max_candidate_rows:
            raise ReasoningLimitError(
                f"evaluation exceeded max_candidate_rows={self.limits.max_candidate_rows}"
            )


def _ready(filters, bindings) -> bool:
    return all(item.holds(bindings) for item in filters
               if item.variables <= set(bindings))


class GenericEngine:
    """Stateless positive typed-Datalog evaluator with filter pruning.

    Each synchronous round joins at least one delta relation. Filters are
    checked incrementally as their variables bind, pruning before deeper
    traversal. Only complete closures are returned; limits and cancellation
    behave like the binary engine. Witness collection is not offered here.
    """

    def evaluate(
        self, program: GenericProgram, facts: Iterable[TypedFact | Evidence], *,
        limits: ReasoningLimits, collect_proofs: bool = False,
        cancel: Callable[[], bool] | None = None,
    ) -> GenericResult:
        if not isinstance(program, GenericProgram):
            raise ReasoningError("program must be a GenericProgram")
        if not isinstance(limits, ReasoningLimits):
            raise ReasoningError("limits must be ReasoningLimits")
        if collect_proofs:
            raise ReasoningError("generic evaluation does not collect witnesses")
        check_cancelled(cancel)
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError(f"evaluation exceeded max_rules={limits.max_rules}")
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be an iterable of TypedFact values") from exc
        initial: set[TypedFact] = set()
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_input_facts={limits.max_input_facts}")
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, TypedFact):
                raise ReasoningError("facts must contain only TypedFact values")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
        full = _TypedIndex(sorted(initial, key=_sort_key))
        delta = full
        del initial
        dispatch: dict[str, list[tuple[GenericRule, int]]] = defaultdict(list)
        for rule in program.rules:
            seen: set[TypedAtom] = set()
            for position, atom in enumerate(rule.body):
                if atom not in seen:
                    dispatch[atom.predicate].append((rule, position))
                    seen.add(atom)
        iterations = 0
        budget = _TypedBudget(limits)
        while delta.facts:
            check_cancelled(cancel)
            active = [(rule, position) for predicate in sorted(delta.by_predicate)
                      for rule, position in dispatch.get(predicate, ())
                      if all(atom.predicate in full.by_predicate for atom in rule.body)]
            if not active:
                break
            if iterations >= limits.max_iterations:
                raise ReasoningLimitError(f"evaluation exceeded max_iterations={limits.max_iterations}")
            added: set[TypedFact] = set()
            for rule, pivot in active:
                for bindings in self._matches(rule, pivot, full, delta, budget):
                    conclusion = TypedFact(rule.head.predicate, tuple(
                        bindings[term] if isinstance(term, Variable) else term for term in rule.head.args))
                    if conclusion in full.facts or conclusion in added:
                        continue
                    if len(full.facts) + len(added) >= limits.max_facts:
                        raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
                    added.add(conclusion)
            iterations += 1
            ordered = sorted(added, key=_sort_key)
            for fact in ordered:
                full.add(fact)
            delta = _TypedIndex(ordered)
        return GenericResult(tuple(full.facts), iterations, program.digest)

    def _matches(self, rule, pivot, full, delta, budget):
        remaining = tuple(index for index in range(len(rule.body)) if index != pivot)
        stack = [(dict(), remaining, pivot, iter(delta.candidates(rule.body[pivot], dict())))]
        while stack:
            bindings, remaining, position, candidates = stack[-1]
            try:
                fact = next(candidates)
            except StopIteration:
                stack.pop()
                continue
            budget.candidate()
            updated = _unify(rule.body[position], fact, bindings)
            if updated is None or not _ready(rule.filters, updated):
                continue
            if not remaining:
                yield updated
                continue
            # Stable ties retain original body position, including repeated atoms.
            chosen, bucket = remaining[0], full.candidates(rule.body[remaining[0]], updated)
            for other in remaining[1:]:
                other_bucket = full.candidates(rule.body[other], updated)
                if len(other_bucket) < len(bucket):
                    chosen, bucket = other, other_bucket
                if not bucket:
                    break
            if bucket:
                stack.append((updated, tuple(index for index in remaining if index != chosen),
                              chosen, iter(bucket)))
