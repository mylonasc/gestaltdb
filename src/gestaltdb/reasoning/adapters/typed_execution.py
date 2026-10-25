"""Authenticated positive typed-point application execution, no publication.

Always compute from complete active assertion input, not a supplied closure.
Source admission, state compatibility and same-engine recomputation remain
distinct from an independent proof verifier.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..evidence_ledger import AssertionVersion
from ..executors import ExecutorRequest, TYPED_PROFILE, TYPED_DICTIONARY, TYPED_INDEXED_PYTHON, select_executor
from ..generation import AdmissionRequest
from ..model import ReasoningError, ReasoningLimits, check_cancelled
from .authenticated_sessions import _validate
from .execution import TypedExecutionOutcome, execute_typed_closure
from .session_generation import SessionAdmissionLimits, SessionGenerationBinding, bind_session_generation


@dataclass(frozen=True)
class AuthenticatedTypedExecution:
    outcome: TypedExecutionOutcome
    binding: SessionGenerationBinding
    evidence: tuple[AssertionVersion, ...]


def execute_authenticated_session(graph, session, *, expected_generation, expected_session_digest,
        limits: ReasoningLimits, executor_request=ExecutorRequest(profile=TYPED_PROFILE),
        freshness="latest", admission_limits=SessionAdmissionLimits(), cancel=None) -> AuthenticatedTypedExecution:
    """Re-admit exact session input, dispatch fresh typed closure, compare state.

    Retains exact independent input versions; GenericEngine returns set closure,
    not witnesses or maintenance supports. No file/database writes or implicit
    rebuild/advance. Historical input is explicit; latest checked before return
    under the temporal writer guard. Not a continuing freshness lease.
    """
    _validate(graph, expected_generation, expected_session_digest, admission_limits, freshness, cancel)
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("ReasoningLimits required")
    decision = select_executor(executor_request)
    if decision.kind != TYPED_INDEXED_PYTHON:
        raise ReasoningError("authenticated typed execution requires the positive typed profile")
    if expected_generation.semantics_profile != TYPED_PROFILE or expected_generation.dictionary_id != TYPED_DICTIONARY:
        raise ReasoningError("typed profile/representation mismatch; rebind or explicitly recode")
    store, serializer = graph.store, graph.serializer
    with graph.temporal.write_guard():
        binding = bind_session_generation(graph, session, freshness=freshness, limits=admission_limits, cancel=cancel)
        expected_generation.require_compatible(binding.identity)
        if expected_session_digest != binding.session_digest:
            raise ReasoningError("session state does not match expected_session_digest")
        retired = set(session.ledger.retired)
        evidence = []
        for version in session.ledger.versions:
            check_cancelled(cancel)
            if version.version_id not in retired:
                evidence.append(version)
        outcome = execute_typed_closure(session.program, (v.fact for v in evidence), limits=limits,
            context=binding.identity.context, executor_request=executor_request, generation=binding.identity,
            admission_request=AdmissionRequest("closure", decision.kind, TYPED_DICTIONARY), cancel=cancel)
        if set(outcome.result.facts) != set(session.facts):
            raise ReasoningError("session closure differs from fresh typed execution; rebuild explicitly")
        check_cancelled(cancel)
        binding.source_binding.verify_source(graph, freshness=freshness)
        if graph.store is not store or graph.serializer is not serializer:
            raise ReasoningError("typed source store/decoder was rebound")
        return AuthenticatedTypedExecution(outcome, binding, tuple(evidence))
