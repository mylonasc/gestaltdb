"""Validated typed closures and scoped negation diagnostics.

Re-exported by ``typed`` for compatibility. Runtime model imports are local so
the value module and evaluator can be imported independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import ReasoningError, _text
from .terms import Literal

if TYPE_CHECKING:
    from .typed import TypedFact
    from .typed_proofs import TypedProofTrace


@dataclass(frozen=True)
class GenericResult:
    """Complete closure in canonical order; evaluators never return partial runs."""

    facts: tuple[TypedFact, ...]
    iterations: int
    program_digest: str
    proofs: TypedProofTrace | None = None

    def __post_init__(self) -> None:
        from .typed import TypedFact, _sort_key

        if not isinstance(self.facts, (tuple, list)):
            raise ReasoningError("result facts must be a tuple or list")
        if not all(isinstance(fact, TypedFact) for fact in self.facts):
            raise ReasoningError("result facts must contain only TypedFact values")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise ReasoningError("iterations must be a non-negative integer")
        _text(self.program_digest, "program_digest")
        object.__setattr__(self, "facts", tuple(sorted(set(self.facts), key=_sort_key)))
        if self.proofs is not None:
            from .typed_proofs import TypedProofTrace

            if not isinstance(self.proofs, TypedProofTrace):
                raise ReasoningError("proofs must be a TypedProofTrace or None")
            if self.proofs.program.digest != self.program_digest:
                raise ReasoningError("proof program does not match the result")
            if any(node.fact not in self or any(premise not in self for premise in node.premises)
                   or any(operand not in self for derivation in node.aggregates
                          for operand in derivation.operands)
                   for node in self.proofs.nodes):
                raise ReasoningError("proof facts must belong to the result closure")
            if not self.proofs.truncated and len(self.proofs.nodes) != len(self.facts):
                raise ReasoningError("untruncated proofs must cover the result closure")

    def __contains__(self, fact: object) -> bool:
        from .typed import TypedFact

        return isinstance(fact, TypedFact) and fact in set(self.facts)


@dataclass(frozen=True)
class NegationWitness:
    """One exact blocking presence check for a failed negated condition.

    The witness carries the closed-world scope and the horizon identity of the
    pinned input fact set — never a fabricated negative fact. ``position`` is
    the negated body occurrence; ``args`` identify the present blocking fact.
    ``outcome`` is always ``present``. Successful absence checks enabling a
    conclusion are not recorded by this why-not diagnostic.
    """

    rule_id: str
    position: int
    predicate: str
    args: tuple[str | Literal, ...]
    scope: tuple[str, ...]
    horizon: str
    outcome: str = "present"

    def __post_init__(self) -> None:
        _text(self.rule_id, "rule_id")
        if isinstance(self.position, bool) or not isinstance(self.position, int) or self.position < 0:
            raise ReasoningError("position must be a non-negative integer")
        _text(self.predicate, "predicate")
        if not isinstance(self.args, (tuple, list)):
            raise ReasoningError("args must be a tuple or list")
        for value in self.args:
            if not isinstance(value, (str, Literal)) or (isinstance(value, str) and not value):
                raise ReasoningError("witness args must be entities or literals")
        object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.scope, (tuple, list)) or not self.scope:
            raise ReasoningError("scope must be a non-empty tuple of predicates")
        for predicate in self.scope:
            _text(predicate, "scope predicate")
        object.__setattr__(self, "scope", tuple(self.scope))
        _text(self.horizon, "horizon")
        if self.outcome != "present":
            raise ReasoningError("negation blocker witness outcome must be present")


@dataclass(frozen=True)
class StratifiedRun:
    """Stratified closure plus its component schedule and blocking witnesses."""

    result: GenericResult
    strata: tuple[tuple[str, ...], ...]
    component_rounds: tuple[int, ...]
    witnesses: tuple[NegationWitness, ...]
    truncated: bool
    proofs: TypedProofTrace | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, GenericResult):
            raise ReasoningError("result must be a GenericResult")
        if not isinstance(self.strata, (tuple, list)) or not all(
                isinstance(component, (tuple, list)) and component
                and all(isinstance(name, str) and name for name in component)
                for component in self.strata):
            raise ReasoningError("strata must be non-empty predicate groups")
        object.__setattr__(self, "strata", tuple(tuple(component) for component in self.strata))
        if not isinstance(self.component_rounds, (tuple, list)) or not all(
                isinstance(count, int) and not isinstance(count, bool) and count >= 0
                for count in self.component_rounds):
            raise ReasoningError("component_rounds must be non-negative integers")
        if len(self.component_rounds) != len(self.strata):
            raise ReasoningError("component_rounds must cover every stratum")
        object.__setattr__(self, "component_rounds", tuple(self.component_rounds))
        if not isinstance(self.witnesses, (tuple, list)) or not all(
                isinstance(item, NegationWitness) for item in self.witnesses):
            raise ReasoningError("witnesses must contain only NegationWitness values")
        object.__setattr__(self, "witnesses", tuple(self.witnesses))
        if not isinstance(self.truncated, bool):
            raise ReasoningError("truncated must be a bool")
        if self.proofs is not None:
            from .typed_proofs import TypedProofTrace

            if not isinstance(self.proofs, TypedProofTrace):
                raise ReasoningError("proofs must be a TypedProofTrace or None")
            if self.proofs.program.digest != self.result.program_digest:
                raise ReasoningError("proof program does not match the result")
            if any(node.fact not in self.result or any(premise not in self.result for premise in node.premises)
                   or any(operand not in self.result for derivation in node.aggregates
                          for operand in derivation.operands)
                   for node in self.proofs.nodes):
                raise ReasoningError("proof facts must belong to the result closure")
            if not self.proofs.truncated and len(self.proofs.nodes) != len(self.result.facts):
                raise ReasoningError("untruncated proofs must cover the result closure")
