"""Bounded tabled demand over positive binary rules; source reads are explicit.

The callback supplies asserted evidence only. Monotone demand/fact rounds reuse
the reference engine's indexes and unification; no storage imports or implicit
source access. Answers are complete for the goal, not a complete world closure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, cast

from .engine import _Index, _unify, _value
from .model import Atom, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, Variable, check_cancelled
from .protocols import Evidence


@dataclass(frozen=True)
class FrontierRequest:
    """One logical-predicate read; None endpoints are unrestricted."""

    predicate: str
    subject: str | None = None
    object: str | None = None


@dataclass(frozen=True)
class FrontierAnswer:
    """Complete goal answers and exact fetched inputs, not proofs/all supports."""

    facts: tuple[Fact, ...]
    evidence: tuple[Evidence, ...]
    program_digest: str
    iterations: int
    requests: int
    input_rows: int
    candidate_rows: int
    known_facts: int


def evaluate_frontier(program: Program, goal: Atom,
                      read: Callable[[FrontierRequest], Iterable[Evidence]], *,
                      limits: ReasoningLimits, max_requests: int = 10_000,
                      cancel: Callable[[], bool] | None = None) -> FrontierAnswer:
    """Table positive binary demand with a finite monotone request/fact fixpoint.

    Rule body order is the sideways binding strategy. A disconnected atom may
    require an unbound predicate read. Every demanded predicate, including rule
    heads, is read for assertions. Repeated goal variables are checked on output.
    Candidate work counts scheduler/rule/body visits and indexed unifications;
    it is not the reference evaluator's attempted-unification counter.
    """
    if not isinstance(program, Program) or not isinstance(goal, Atom):
        raise ReasoningError("positive binary Program and Atom goal required")
    if not isinstance(limits, ReasoningLimits) or not callable(read):
        raise ReasoningError("ReasoningLimits and callable evidence reader required")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise ReasoningError("max_requests must be a positive integer")
    if len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    check_cancelled(cancel)
    by_head = defaultdict(list)
    for rule in program.rules:
        by_head[rule.head.predicate].append(rule)
    index = _Index()
    evidence: dict[str, Evidence] = {}
    requested: dict[FrontierRequest, None] = {}
    completed = set()
    work = input_rows = 0

    def tick():
        nonlocal work
        check_cancelled(cancel)
        work += 1
        if work > limits.max_candidate_rows:
            raise ReasoningLimitError("frontier exceeded max_candidate_rows")

    def demand(predicate, subject, obj):
        request = FrontierRequest(predicate, subject, obj)
        if request not in requested:
            if len(requested) >= max_requests:
                raise ReasoningLimitError("frontier exceeded max_requests")
            requested[request] = None

    def head_bindings(atom, request):
        bindings = {}
        for term, value in ((atom.subject, request.subject), (atom.object, request.object)):
            if value is None:
                continue
            if isinstance(term, Variable):
                if term in bindings and bindings[term] != value:
                    return None
                bindings[term] = value
            elif term != value:
                return None
        return bindings

    demand(goal.predicate, _value(goal.subject, {}), _value(goal.object, {}))
    for iteration in range(1, limits.max_iterations + 1):
        tick()
        current = tuple(requested)
        for request in current:
            if request in completed:
                continue
            tick()
            iterator = iter(read(request))
            while True:
                check_cancelled(cancel)
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                tick()
                input_rows += 1
                if input_rows > limits.max_input_facts:
                    raise ReasoningLimitError("frontier exceeded max_input_facts")
                if not isinstance(item, Evidence):
                    raise ReasoningError("frontier reader must yield Evidence")
                fact = item.fact
                if (fact.predicate != request.predicate
                        or request.subject is not None and fact.subject != request.subject
                        or request.object is not None and fact.object != request.object):
                    raise ReasoningError("evidence falls outside frontier request")
                previous = evidence.get(item.reference_id)
                if previous is not None and previous != item:
                    raise ReasoningError("evidence reference changed fact")
                evidence[item.reference_id] = item
                if fact not in index.facts:
                    if len(index.facts) >= limits.max_facts:
                        raise ReasoningLimitError("frontier exceeded max_facts")
                    index.add(fact)
            completed.add(request)
        added: set[Fact] = set()
        for request in current:
            tick()
            for rule in by_head[request.predicate]:
                tick()
                initial = head_bindings(rule.head, request)
                if initial is None:
                    continue
                # DFS frames bound intermediate memory to the body depth.
                stack: list[tuple[int, dict[Variable, str], Iterator[Fact] | None]] = [(0, initial, None)]
                while stack:
                    tick()
                    position, bindings, candidates = stack[-1]
                    if position == len(rule.body):
                        fact = Fact(cast(str, _value(rule.head.subject, bindings)), rule.head.predicate,
                                    cast(str, _value(rule.head.object, bindings)))
                        if fact not in index.facts and fact not in added:
                            if len(index.facts) + len(added) >= limits.max_facts:
                                raise ReasoningLimitError("frontier exceeded max_facts")
                            added.add(fact)
                        stack.pop()
                        continue
                    atom = rule.body[position]
                    if candidates is None:
                        demand(atom.predicate, _value(atom.subject, bindings), _value(atom.object, bindings))
                        candidates = iter(index.candidates(atom, bindings))
                        stack[-1] = (position, bindings, candidates)
                    try:
                        fact = next(candidates)
                    except StopIteration:
                        stack.pop()
                        continue
                    updated = _unify(atom, fact, bindings)
                    if updated is not None:
                        stack.append((position + 1, updated, None))
        for fact in sorted(added):
            index.add(fact)
        if not added and len(requested) == len(current):
            answers = tuple(sorted(fact for fact in index.candidates(goal, {})
                                   if _unify(goal, fact, {}) is not None))
            check_cancelled(cancel)
            return FrontierAnswer(answers, tuple(evidence[key] for key in sorted(evidence)),
                                  program.digest, iteration, len(requested), input_rows, work, len(index.facts))
    raise ReasoningLimitError("frontier exceeded max_iterations")
