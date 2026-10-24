"""Opt-in executor-aware closure evaluation with admission and telemetry.

Existing engine call sites are unchanged. This is the single place where an
explicit ``ExecutorRequest`` becomes a verified decision, an optional bound
generation becomes an admission check, and every supported engine reports the
same stage counters. There is no automatic switching: the indexed reference
wins unless the caller explicitly prefers another measured mode, and
unsupported combinations fail closed instead of degrading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..engine import DatalogEngine
from ..executors import (
    EXECUTORS,
    INDEXED_PYTHON,
    INDEXED_SCC,
    POLARS_PROTOTYPE,
    ExecutorRequest,
    select_executor,
)
from ..generation import AdmissionRequest, GenerationIdentity, admit
from ..model import (
    EvaluationResult,
    Fact,
    Program,
    ReasoningError,
    ReasoningLimitError,
    ReasoningLimits,
    _text,
    check_cancelled,
)
from ..plans import PlannedDatalogEngine, PlannedRun, PreparedProgram
from ..protocols import Evidence, InputContext
from ..relational import CompilationIdentity
from .polars import PolarsDatalogPrototype, PolarsRun, _polars


@dataclass(frozen=True)
class StageTelemetry:
    """Uniform per-invocation counters; engine-specific detail stays behind.

    ``rationale`` records the exact decision codes. ``admission_digest`` is
    present only when a generation was admitted. ``plan_digest`` is present
    only for prepared-plan execution. Timings, if any, belong to the
    engine-native reports, never here.
    """

    executor: str
    mode: str
    rationale: tuple[str, ...]
    admission_digest: str | None
    plan_digest: str | None
    input_facts: int
    output_facts: int
    proofs: bool

    def __post_init__(self) -> None:
        if self.executor not in EXECUTORS:
            raise ReasoningError("telemetry executor must be a known executor")
        if self.mode not in ("closure", "proofs"):
            raise ReasoningError("telemetry mode must be closure or proofs")
        if not isinstance(self.rationale, (tuple, list)) or not self.rationale:
            raise ReasoningError("rationale must be a non-empty tuple of codes")
        for code in self.rationale:
            _text(code, "rationale code")
        object.__setattr__(self, "rationale", tuple(self.rationale))
        if self.admission_digest is not None:
            _text(self.admission_digest, "admission_digest")
        if self.plan_digest is not None:
            _text(self.plan_digest, "plan_digest")
        for name in ("input_facts", "output_facts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReasoningError(f"{name} must be a non-negative integer")
        if not isinstance(self.proofs, bool):
            raise ReasoningError("proofs must be a bool")


@dataclass(frozen=True)
class ExecutionOutcome:
    """One admitted closure: engine result plus its uniform telemetry."""

    result: EvaluationResult
    telemetry: StageTelemetry


def _telemetry(executor: str, mode: str, rationale: tuple[str, ...], admission_digest: str | None,
               plan_digest: str | None, inputs: int, result: EvaluationResult,
               proofs: bool) -> StageTelemetry:
    return StageTelemetry(executor, mode, rationale, admission_digest, plan_digest,
                          inputs, len(result.facts), proofs)


def execute_closure(program: Program, facts: Iterable[Fact | Evidence], *,
                    executor_request: ExecutorRequest = ExecutorRequest(),
                    admission_request: AdmissionRequest | None = None,
                    generation: GenerationIdentity | None = None,
                    plan: PreparedProgram | None = None,
                    context: InputContext | None = None,
                    limits: ReasoningLimits, collect_proofs: bool = False,
                    cancel: Callable[[], bool] | None = None) -> ExecutionOutcome:
    """Evaluate one binary closure through an explicitly decided executor.

    The decision is verified, not assumed: ``select_executor`` resolves the
    executor, and a bound generation is admitted with exact dictionary, mode
    and executor compatibility. Prepared-plan execution requires the caller to
    supply the matching ``PreparedProgram``; Polars execution requires an
    explicit ``InputContext`` and a truly available runtime. Omitting the
    preference keeps the indexed reference even when Polars is installed.
    """
    if not isinstance(program, Program):
        raise ReasoningError("Program required")
    if not isinstance(executor_request, ExecutorRequest):
        raise ReasoningError("executor_request must be an ExecutorRequest")
    if admission_request is not None and not isinstance(admission_request, AdmissionRequest):
        raise ReasoningError("admission_request must be an AdmissionRequest or None")
    if generation is not None and not isinstance(generation, GenerationIdentity):
        raise ReasoningError("generation must be a GenerationIdentity or None")
    if plan is not None and not isinstance(plan, PreparedProgram):
        raise ReasoningError("plan must be a PreparedProgram or None")
    if context is not None and not isinstance(context, InputContext):
        raise ReasoningError("context must be an InputContext or None")
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("limits must be ReasoningLimits")
    if not isinstance(collect_proofs, bool):
        raise ReasoningError("collect_proofs must be a bool")
    check_cancelled(cancel)
    mode = "proofs" if collect_proofs else "closure"
    if executor_request.mode != mode:
        raise ReasoningError("executor request mode must match the requested proof behavior")
    decision = select_executor(executor_request)
    if len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    if decision.kind == INDEXED_SCC and plan is None:
        raise ReasoningError("prepared-plan execution requires an explicit PreparedProgram")
    if plan is not None and decision.kind != INDEXED_SCC:
        raise ReasoningError("a prepared plan requires indexed-scc execution")
    if (decision.kind != INDEXED_PYTHON or generation is not None) and context is None:
        raise ReasoningError("bound or non-reference execution requires an explicit InputContext")
    identity = None if context is None else CompilationIdentity(
        program.digest, context, "external-strings-v1", executor_request.profile, decision.kind)
    if plan is not None:
        plan.identity.require_compatible(identity)
    if decision.kind == POLARS_PROTOTYPE:
        _polars()
    admission_digest: str | None = None
    if generation is not None or admission_request is not None:
        if generation is None or admission_request is None:
            raise ReasoningError("generation and admission_request are required together")
        if admission_request.mode != mode:
            raise ReasoningError("admission mode must match the requested proof behavior")
        if admission_request.executor != decision.kind:
            raise ReasoningError("admission executor must match the selected executor")
        generation.require_compilation(identity)
        admission = admit(generation, admission_request)
        admission_digest = admission.generation_digest
    rationale = decision.rationale
    # Materialize once up front: generators are single-pass and every engine
    # below must observe the same input multiset for parity.
    inputs = _materialize_inputs(facts)
    if decision.kind == INDEXED_PYTHON:
        result = DatalogEngine().evaluate(program, inputs, limits=limits,
                                          collect_proofs=collect_proofs, cancel=cancel)
        return ExecutionOutcome(result, _telemetry(decision.kind, mode, rationale, admission_digest,
                                                   None, len(inputs), result, collect_proofs))
    if decision.kind == INDEXED_SCC:
        run: PlannedRun = PlannedDatalogEngine().evaluate(
            plan, inputs, identity=identity, limits=limits,
            collect_proofs=collect_proofs, cancel=cancel)
        return ExecutionOutcome(run.result, _telemetry(decision.kind, mode, rationale, admission_digest,
                                                       plan.digest, len(inputs), run.result,
                                                       collect_proofs))
    if context is None:
        raise ReasoningError("polars execution requires an explicit InputContext")
    run_polars: PolarsRun = PolarsDatalogPrototype().evaluate(
        program, inputs, context=context, limits=limits, cancel=cancel)
    return ExecutionOutcome(run_polars.result, _telemetry(decision.kind, mode, rationale, admission_digest,
                                                          None, len(inputs), run_polars.result, False))


def _materialize_inputs(facts: Iterable[Fact | Evidence]) -> list[Fact]:
    try:
        items = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    out = []
    for value in items:
        fact = value.fact if isinstance(value, Evidence) else value
        if not isinstance(fact, Fact):
            raise ReasoningError("facts must contain only Fact or Evidence values")
        out.append(fact)
    return out
