"""Standalone bounded positive-Datalog evaluation, with no storage side effects.

Each synchronous round joins at least one delta relation. Predicate/endpoint
indexes restrict candidates; remaining atoms are chosen by indexed bucket size.
Body traversal uses an explicit stack, not Python recursion or eagerly collected
intermediate join tables. All run state belongs to one ``evaluate`` invocation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable, Iterator, cast

from .model import (
    Atom, EvaluationResult, Fact, Program, ReasoningError, ReasoningLimitError,
    ReasoningLimits, Rule, Term, Variable, check_cancelled,
)
from .proofs import _ProofCollector
from .protocols import Evidence


class _Index:
    def __init__(self, facts: Iterable[Fact] = ()) -> None:
        self.facts: set[Fact] = set()
        self.by_predicate: dict[str, list[Fact]] = defaultdict(list)
        self.by_subject: dict[tuple[str, str], list[Fact]] = defaultdict(list)
        self.by_object: dict[tuple[str, str], list[Fact]] = defaultdict(list)
        for fact in facts:
            self.add(fact)

    def add(self, fact: Fact) -> None:
        if fact in self.facts:
            return
        self.facts.add(fact)
        self.by_predicate[fact.predicate].append(fact)
        self.by_subject[fact.predicate, fact.subject].append(fact)
        self.by_object[fact.predicate, fact.object].append(fact)

    def candidates(self, atom: Atom, bindings: dict[Variable, str]) -> list[Fact] | tuple[Fact, ...]:
        subject = _value(atom.subject, bindings)
        object_value = _value(atom.object, bindings)
        if subject is not None and object_value is not None:
            fact = Fact(subject, atom.predicate, object_value)
            return (fact,) if fact in self.facts else ()
        if subject is not None:
            return self.by_subject.get((atom.predicate, subject), ())
        if object_value is not None:
            return self.by_object.get((atom.predicate, object_value), ())
        return self.by_predicate.get(atom.predicate, ())


def _value(term: Term, bindings: dict[Variable, str]) -> str | None:
    return bindings.get(term) if isinstance(term, Variable) else term


def _unify(atom: Atom, fact: Fact, bindings: dict[Variable, str]) -> dict[Variable, str] | None:
    updated = dict(bindings)
    for term, value in ((atom.subject, fact.subject), (atom.object, fact.object)):
        if isinstance(term, Variable):
            previous = updated.get(term)
            if previous is not None and previous != value:
                return None
            updated[term] = value
        elif term != value:
            return None
    return updated


class _Budget:
    """Per-evaluation candidate-row budget gate.

    Intentionally parallel to ``typed._TypedBudget`` (#373 dispositions):
    private, identical, and not worth a cross-module dependency.
    """
    def __init__(self, limits: ReasoningLimits) -> None:
        self.limits = limits
        self.candidate_rows = 0

    def candidate(self) -> None:
        self.candidate_rows += 1
        if self.candidate_rows > self.limits.max_candidate_rows:
            raise ReasoningLimitError(
                f"evaluation exceeded max_candidate_rows={self.limits.max_candidate_rows}"
            )


def _choose(
    rule: Rule, remaining: tuple[int, ...], bindings: dict[Variable, str], full: _Index,
) -> tuple[int, list[Fact] | tuple[Fact, ...]]:
    # Stable ties retain original body position, including for repeated atoms.
    position = remaining[0]
    candidates = full.candidates(rule.body[position], bindings)
    for other in remaining[1:]:
        bucket = full.candidates(rule.body[other], bindings)
        if len(bucket) < len(candidates):
            position, candidates = other, bucket
        if not candidates:
            break
    return position, candidates


def _matches(
    rule: Rule, pivot: int, full: _Index, delta: _Index, budget: _Budget,
    *, track_premises: bool = False, observer=None,
) -> Iterator[tuple[dict[Variable, str], list[Fact | None] | None]]:
    remaining = tuple(index for index in range(len(rule.body)) if index != pivot)
    # One invocation-local path buffer, not a copy per intermediate binding.
    # Consumers must snapshot it before requesting another match.
    premises: list[Fact | None] | None = [None] * len(rule.body) if track_premises else None
    # Frames retain only the current DFS path; relation buckets are not copied.
    first = delta.candidates(rule.body[pivot], {})
    if observer is not None:
        observer.lookup(rule, pivot, True, {}, first)
    stack: list[tuple[dict[Variable, str], tuple[int, ...], int, Iterator[Fact]]] = [({}, remaining, pivot, iter(first))]
    while stack:
        bindings, remaining, position, candidates = stack[-1]
        try:
            fact = next(candidates)
        except StopIteration:
            stack.pop()
            continue
        budget.candidate()
        updated = _unify(rule.body[position], fact, bindings)
        if observer is not None:
            observer.attempt(rule, position, position == pivot, bindings, updated is not None)
        if updated is None:
            continue
        if premises is not None:
            premises[position] = fact
        if not remaining:
            yield updated, premises
            continue
        chosen, bucket = _choose(rule, remaining, updated, full)
        if observer is not None:
            observer.lookup(rule, chosen, False, updated, bucket)
        if bucket:
            stack.append((
                updated, tuple(index for index in remaining if index != chosen),
                chosen, iter(bucket),
            ))


class DatalogEngine:
    """Stateless, deterministic positive-Datalog evaluator.

    Only complete closures are returned. Limits apply to each invocation;
    failure does not poison later runs. Proof collection is opt-in and bounded
    independently: truncating a witness trace never truncates logical closure.

    Examples:
        Infer a relationship from a positive rule, without a database::

            >>> from gestaltdb.reasoning.engine import DatalogEngine
            >>> from gestaltdb.reasoning.model import Atom, Fact, Program, Rule, Variable, ReasoningLimits
            >>> x, y = Variable("x"), Variable("y")
            >>> program = Program([Rule("parent-is-ancestor", [Atom(x, "parent", y)], Atom(x, "ancestor", y))])
            >>> result = DatalogEngine().evaluate(program, [Fact("alice", "parent", "bob")],
            ...     limits=ReasoningLimits(), collect_proofs=True)
            >>> conclusion = Fact("alice", "ancestor", "bob")
            >>> assert conclusion in result
            >>> assert not result.explain(conclusion).truncated

    For n-ary relations, strict literals, and filters use ``GenericEngine``.
    """

    def evaluate(
        self, program: Program, facts: Iterable[Fact | Evidence], *, limits: ReasoningLimits,
        collect_proofs: bool = False, cancel: Callable[[], bool] | None = None,
    ) -> EvaluationResult:
        if not isinstance(program, Program):
            raise ReasoningError("program must be a Program")
        if not isinstance(limits, ReasoningLimits):
            raise ReasoningError("limits must be ReasoningLimits")
        if not isinstance(collect_proofs, bool):
            raise ReasoningError("collect_proofs must be a bool")
        check_cancelled(cancel)
        if len(program.rules) > limits.max_rules:
            raise ReasoningLimitError(f"evaluation exceeded max_rules={limits.max_rules}")
        try:
            iterator = iter(facts)
        except TypeError as exc:
            raise ReasoningError("facts must be an iterable of Fact values") from exc
        initial: set[Fact] = set()
        collector = _ProofCollector(program, limits) if collect_proofs else None
        for count, value in enumerate(iterator, 1):
            if count > limits.max_input_facts:
                raise ReasoningLimitError(
                    f"evaluation exceeded max_input_facts={limits.max_input_facts}"
                )
            if count % 1024 == 0:
                check_cancelled(cancel)
            fact = value.fact if isinstance(value, Evidence) else value
            if not isinstance(fact, Fact):
                raise ReasoningError("facts must contain only Fact or Evidence values")
            initial.add(fact)
            if len(initial) > limits.max_facts:
                raise ReasoningLimitError(f"evaluation exceeded max_facts={limits.max_facts}")
            if collector is not None:
                collector.input(fact, value.reference_id if isinstance(value, Evidence) else None)

        if collector is not None:
            collector.start()

        full = _Index(sorted(initial))
        delta = full
        del initial
        dispatch: dict[str, list[tuple[Rule, int]]] = defaultdict(list)
        for rule in program.rules:
            seen: set[Atom] = set()
            for position, atom in enumerate(rule.body):
                # Identical atoms require the same ground fact under a binding;
                # one delta pivot suffices without changing body provenance.
                if atom not in seen:
                    dispatch[atom.predicate].append((rule, position))
                    seen.add(atom)
        iterations = 0
        budget = _Budget(limits)
        while delta.facts:
            check_cancelled(cancel)
            active = [
                (rule, position)
                for predicate in sorted(delta.by_predicate)
                for rule, position in dispatch.get(predicate, ())
                if all(atom.predicate in full.by_predicate for atom in rule.body)
            ]
            if not active:
                break
            if iterations >= limits.max_iterations:
                raise ReasoningLimitError(
                    f"evaluation exceeded max_iterations={limits.max_iterations}"
                )
            added: set[Fact] = set()
            for rule, pivot in active:
                track = collector is not None and collector.can_record(rule)
                for bindings, premises in _matches(
                    rule, pivot, full, delta, budget, track_premises=track,
                ):
                    # Safe heads ensure every Variable has a binding here.
                    subject = _value(rule.head.subject, bindings)
                    object_value = _value(rule.head.object, bindings)
                    conclusion = Fact(cast(str, subject), rule.head.predicate, cast(str, object_value))
                    if conclusion in full.facts or conclusion in added:
                        continue
                    if len(full.facts) + len(added) >= limits.max_facts:
                        raise ReasoningLimitError(
                            f"evaluation exceeded max_facts={limits.max_facts}"
                        )
                    added.add(conclusion)
                    if collector is not None:
                        collector.derived(conclusion, rule, premises)
            iterations += 1
            ordered = sorted(added)
            for fact in ordered:
                full.add(fact)
            delta = _Index(ordered)

        return EvaluationResult(
            tuple(full.facts), iterations, program.digest,
            collector.finish() if collector is not None else None,
        )
