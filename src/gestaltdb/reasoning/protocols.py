"""Inward-facing adapter and evaluator contracts; no graph/temporal imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .model import EvaluationResult, Fact, Program, ReasoningError, ReasoningLimits, _text


@dataclass(frozen=True)
class InputContext:
    """Opaque adapter-selected identity for one compatible evidence context.

    A context ID must authenticate/identify the adapter's pinned input. These
    strings alone do not verify provenance. Adapters must reject incompatible
    snapshots and apply world/source policy before handing facts to the engine.
    """

    context_id: str
    world: str
    ontology_revision: str
    source_policy_id: str
    mapping_id: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _text(getattr(self, name), name)


@dataclass(frozen=True)
class Evidence:
    """One immutable input reference; many references may support one fact."""

    fact: Fact
    reference_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.fact, Fact):
            raise ReasoningError("evidence fact must be a Fact")
        _text(self.reference_id, "reference_id")


class FactSource(Protocol):
    """Already-pinned single-context evidence, consumed under explicit bounds.

    Ontology compilation/evaluation never invokes this adapter implicitly.
    Consumers enforce a bound while iterating, before collecting all evidence.
    The source owns its iteration lifetime and preserves a pinned view while
    active. It may borrow a caller-managed graph/view without closing either.
    Iteration must not silently repin.
    """

    @property
    def context(self) -> InputContext: ...

    def iter_evidence(self) -> Iterable[Evidence]: ...


class Evaluator(Protocol):
    """Non-persisting deterministic complete-closure service.

    Consume at most ``max_input_facts + 1`` values to detect an oversized input.
    Values are Fact or Evidence. Duplicate inputs count toward that bound but
    not logical multiplicity. No database, clock or source access is implied.
    ``cancel`` is cooperative: ``None`` disables it; engines check it between
    bounded work units and raise ``ReasoningCancelledError`` without partial
    results. Running native kernels are never interrupted mid-call.
    """

    def evaluate(
        self,
        program: Program,
        facts: Iterable[Fact | Evidence],
        *,
        limits: ReasoningLimits,
        collect_proofs: bool = False,
        cancel: Callable[[], bool] | None = None,
    ) -> EvaluationResult: ...
