"""Immutable, standard-library-only contracts for finite positive Datalog.

Terms are nonempty strings (named entities) or explicit ``Variable`` values.
Predicates are constants. Nothing in this module knows about graph storage,
temporal values, sources, ontologies or optional solvers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING, Callable


def check_cancelled(cancel: Callable[[], bool] | None) -> None:
    """Raise ``ReasoningCancelledError`` when a cooperative cancel fires.

    ``None`` disables cancellation. Non-callable tokens fail closed at the
    first check. Exceptions raised by the token itself propagate unchanged.
    """
    if cancel is None:
        return
    if not callable(cancel):
        raise ReasoningError("cancel must be a callable returning a boolean or None")
    if cancel():
        raise ReasoningCancelledError("evaluation was cancelled; no partial result is available")

if TYPE_CHECKING:
    from .proofs import Explanation, ProofTrace


class ReasoningError(ValueError):
    """Invalid input to the standalone reasoning layer.

    Sibling of top-level ``RuleError``/``ModalError`` under ``ValueError``
    (via :class:`TemporalVersionError`). ``except ReasoningError`` misses
    graph-rule/modal bounds by design; catch
    ``(ReasoningError, TemporalVersionError)`` to cover every
    bounded-evaluation failure.
    """


class ReasoningLimitError(ReasoningError):
    """An inference bound was exceeded; no complete result is available."""


class ReasoningCancelledError(ReasoningError):
    """Cooperative cancellation was requested; no complete result is available.

    Cancellation never yields a partial closure and never poisons later runs.
    """


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReasoningError(f"{name} must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReasoningError(f"{name} must be valid UTF-8 text") from exc
    return value


@dataclass(frozen=True, order=True)
class Variable:
    """Explicit rule variable; a string such as ``'?x'`` is still a constant."""

    name: str

    def __post_init__(self) -> None:
        _text(self.name, "variable name")


Term = str | Variable


def _term(value: object, name: str) -> Term:
    if isinstance(value, Variable):
        return value
    return _text(value, name)


@dataclass(frozen=True, order=True)
class Fact:
    """One ground binary relation assertion over named entities."""

    subject: str
    predicate: str
    object: str

    def __post_init__(self) -> None:
        _text(self.subject, "fact subject")
        _text(self.predicate, "fact predicate")
        _text(self.object, "fact object")


@dataclass(frozen=True)
class Atom:
    """Rule atom with a constant predicate and typed subject/object terms."""

    subject: Term
    predicate: str
    object: Term

    def __post_init__(self) -> None:
        _term(self.subject, "atom subject")
        _text(self.predicate, "atom predicate")
        _term(self.object, "atom object")

    @property
    def variables(self) -> frozenset[Variable]:
        return frozenset(
            term for term in (self.subject, self.object)
            if isinstance(term, Variable)
        )


@dataclass(frozen=True)
class Rule:
    """Safe positive single-head rule; body order is retained for provenance."""

    rule_id: str
    body: tuple[Atom, ...]
    head: Atom

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        if not isinstance(self.body, (tuple, list)):
            raise ReasoningError("rule body must be a tuple or list of atoms")
        body = tuple(self.body)
        if not body or not all(isinstance(atom, Atom) for atom in body):
            raise ReasoningError("rule body must contain at least one Atom")
        if not isinstance(self.head, Atom):
            raise ReasoningError("rule head must be an Atom")
        bound = frozenset().union(*(atom.variables for atom in body))
        if not self.head.variables <= bound:
            raise ReasoningError("unsafe rule: head variables must occur in the body")
        object.__setattr__(self, "body", body)


def _encoded_atom(atom: Atom) -> list[object]:
    def encode(term: Term) -> list[str]:
        return ["variable", term.name] if isinstance(term, Variable) else ["entity", term]

    return [encode(atom.subject), atom.predicate, encode(atom.object)]


@dataclass(frozen=True)
class Program:
    """Validated rule catalog, canonically ordered by unique rule ID."""

    rules: tuple[Rule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.rules, (tuple, list)):
            raise ReasoningError("program rules must be a tuple or list")
        rules = tuple(self.rules)
        if not all(isinstance(rule, Rule) for rule in rules):
            raise ReasoningError("program rules must contain only Rule values")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ReasoningError("program rule IDs must be unique")
        object.__setattr__(self, "rules", tuple(sorted(rules, key=lambda rule: rule.rule_id)))

    @property
    def digest(self) -> str:
        """Versioned structural identity, not logical-equivalence detection.

        Rule IDs, variable names and body order participate in this digest.
        Rule catalog order does not. Ontology revisions have separate identities.
        """
        payload = [
            "gestaltdb-datalog-v1",
            [[rule.rule_id, [_encoded_atom(atom) for atom in rule.body],
              _encoded_atom(rule.head)] for rule in self.rules],
        ]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ReasoningLimits:
    """Deterministic work/storage counters, not exact RSS or time guarantees.

    Input counts include duplicates. Candidate rows count cumulative attempted
    fact/partial-binding joins, including failed matches, across evaluation.
    ``max_facts`` counts distinct input plus derived facts. Proof budgets count
    fact-witness records and premise/reference occurrences, respectively. They
    truncate explanations only; inference-counter exhaustion raises instead.
    """

    max_input_facts: int = 100_000
    max_rules: int = 10_000
    max_iterations: int = 100
    max_candidate_rows: int = 1_000_000
    max_facts: int = 100_000
    max_proof_nodes: int = 10_000
    max_proof_edges: int = 20_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class EvaluationResult:
    """Complete closure, including asserted facts, in canonical fact order.

    Evaluators must raise on inference-limit failure rather than construct a
    partial result. Iterations count executed delta rounds; empty inputs or a
    program with no applicable rules may require zero rounds. Optional proof
    information is an immutable witness trace, never stored in graph records.
    """

    facts: tuple[Fact, ...]
    iterations: int
    program_digest: str
    proofs: ProofTrace | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.facts, (tuple, list)):
            raise ReasoningError("result facts must be a tuple or list")
        if not all(isinstance(fact, Fact) for fact in self.facts):
            raise ReasoningError("result facts must contain only Fact values")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise ReasoningError("iterations must be a non-negative integer")
        _text(self.program_digest, "program_digest")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts))))
        if self.proofs is not None:
            from .proofs import ProofTrace

            if not isinstance(self.proofs, ProofTrace):
                raise ReasoningError("proofs must be a ProofTrace or None")
            if self.proofs.program.digest != self.program_digest:
                raise ReasoningError("proof program does not match the result")
            if any(node.fact not in self or any(premise not in self for premise in node.premises)
                   for node in self.proofs.nodes):
                raise ReasoningError("proof facts must belong to the result closure")
            if not self.proofs.truncated and len(self.proofs.nodes) != len(self.facts):
                raise ReasoningError("untruncated proofs must cover the result closure")

    def __contains__(self, fact: object) -> bool:
        if not isinstance(fact, Fact):
            return False
        position = bisect_left(self.facts, fact)
        return position < len(self.facts) and self.facts[position] == fact

    def match(
        self, *, subject: str | None = None, predicate: str | None = None,
        object: str | None = None,
    ) -> tuple[Fact, ...]:
        """Canonical matches; subject bounds narrow the scan without new indexes."""
        for name, value in (("subject", subject), ("predicate", predicate), ("object", object)):
            if value is not None:
                _text(value, name)
        start, end = 0, len(self.facts)
        if subject is not None:
            start = bisect_left(self.facts, subject, key=lambda fact: fact.subject)
            end = bisect_right(self.facts, subject, key=lambda fact: fact.subject)
        return tuple(
            self.facts[index] for index in range(start, end)
            if (predicate is None or self.facts[index].predicate == predicate)
            and (object is None or self.facts[index].object == object)
        )

    def explain(self, fact: Fact, *, max_nodes: int = 1000) -> Explanation:
        """One bounded witness; never an all-supports or deletion-maintenance API."""
        if not isinstance(fact, Fact):
            raise ReasoningError("explanation root must be a Fact")
        if fact not in self:
            raise KeyError(fact)
        if self.proofs is None:
            raise ReasoningError("proof collection was disabled for this evaluation")
        return self.proofs.explain(fact, max_nodes=max_nodes)
