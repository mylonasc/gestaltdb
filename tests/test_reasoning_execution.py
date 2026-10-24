"""Opt-in executor-aware closure: decision, admission, uniform telemetry."""

from dataclasses import replace

import pytest

from gestaltdb.reasoning import Atom, Fact, Program, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.adapters.execution import StageTelemetry, execute_closure
from gestaltdb.reasoning.executors import (
    INDEXED_PYTHON,
    INDEXED_SCC,
    POLARS_PROTOTYPE,
    ExecutorRequest,
)
from gestaltdb.reasoning.generation import AdmissionRequest, GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.model import ReasoningError
from gestaltdb.reasoning.plans import PreparedProgram
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import CompilationIdentity

X, Y = Variable("x"), Variable("y")
CONTEXT = InputContext("horizon", "world", "revision", "policy", "mapping")
DICTIONARY = "external-strings-v1"


def program():
    return Program([Rule("r", [Atom(X, "edge", Y)], Atom(X, "reach", Y))])


def facts():
    return [Fact("a", "edge", "b"), Fact("b", "edge", "c"), Fact("n", "noise", "u")]


def generation():
    return GenerationIdentity(TemporalSourceIdentity("db", 1, "token", 50),
                              CONTEXT, program().digest, "catalog", "positive-binary-entity-v1",
                              DICTIONARY)


def prepare():
    identity = CompilationIdentity(program().digest, CONTEXT, DICTIONARY,
                                   "positive-binary-entity-v1", "indexed-scc-v1")
    return PreparedProgram(program(), identity)


def test_default_stays_indexed_with_uniform_telemetry():
    outcome = execute_closure(program(), facts(), limits=ReasoningLimits())
    assert outcome.telemetry.executor == INDEXED_PYTHON
    assert outcome.telemetry.mode == "closure" and not outcome.telemetry.proofs
    assert outcome.telemetry.admission_digest is None and outcome.telemetry.plan_digest is None
    assert (outcome.telemetry.input_facts, outcome.telemetry.output_facts) == (3, 5)
    assert outcome.telemetry.rationale == ("reference-default", "smallest-startup-and-rss",
                                           "small-workload-pipeline-fastest")
    assert {f.predicate for f in outcome.result.facts} == {"edge", "noise", "reach"}


def test_scc_plan_matches_indexed_closure_with_plan_telemetry():
    plan = prepare()
    expected = execute_closure(program(), facts(), limits=ReasoningLimits())
    outcome = execute_closure(program(), facts(),
                              executor_request=ExecutorRequest(preferred="indexed-scc"),
                              plan=plan, context=CONTEXT, limits=ReasoningLimits())
    assert outcome.telemetry.executor == INDEXED_SCC
    assert outcome.telemetry.plan_digest == plan.digest
    assert set(outcome.result.facts) == set(expected.result.facts)
    assert outcome.telemetry.input_facts == outcome.telemetry.input_facts == 3


def test_scc_requires_explicit_prepared_plan():
    with pytest.raises(ReasoningError, match="PreparedProgram"):
        execute_closure(program(), facts(),
                        executor_request=ExecutorRequest(preferred="indexed-scc"),
                        limits=ReasoningLimits())


def test_polars_runs_only_on_explicit_available_request_with_context():
    pytest.importorskip("polars")
    request = ExecutorRequest(preferred="polars-prototype", polars_available=True)
    outcome = execute_closure(program(), facts(), executor_request=request,
                              context=CONTEXT, limits=ReasoningLimits())
    assert outcome.telemetry.executor == POLARS_PROTOTYPE
    assert {f.predicate for f in outcome.result.facts} == {"edge", "noise", "reach"}
    indexed = execute_closure(program(), facts(), limits=ReasoningLimits())
    assert set(outcome.result.facts) == set(indexed.result.facts)


def test_polars_unavailable_or_missing_context_fails_closed():
    with pytest.raises(ReasoningError, match="polars_available"):
        execute_closure(program(), facts(),
                        executor_request=ExecutorRequest(preferred="polars-prototype"),
                        context=CONTEXT, limits=ReasoningLimits())
    pytest.importorskip("polars")
    with pytest.raises(ReasoningError, match="InputContext"):
        execute_closure(program(), facts(),
                        executor_request=ExecutorRequest(preferred="polars-prototype",
                                                        polars_available=True),
                        limits=ReasoningLimits())


def test_admission_binds_generation_dictionary_and_mode():
    request = AdmissionRequest("closure", INDEXED_PYTHON, DICTIONARY)
    outcome = execute_closure(program(), facts(), admission_request=request,
                              generation=generation(), context=CONTEXT, limits=ReasoningLimits())
    assert outcome.telemetry.admission_digest == generation().digest
    assert outcome.telemetry.input_facts == 3
    with pytest.raises(ReasoningError, match="dictionary"):
        execute_closure(program(), facts(),
                        admission_request=AdmissionRequest("closure", INDEXED_PYTHON, "other"),
                        generation=generation(), context=CONTEXT, limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="required together"):
        execute_closure(program(), facts(), admission_request=request, limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="mode"):
        execute_closure(program(), facts(),
                        admission_request=AdmissionRequest("proofs", INDEXED_PYTHON, DICTIONARY),
                        generation=generation(), context=CONTEXT, limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="proof"):
        execute_closure(program(), facts(), collect_proofs=True,
                        executor_request=ExecutorRequest(preferred="polars-prototype",
                                                        polars_available=True),
                        context=CONTEXT, limits=ReasoningLimits())


def test_proofs_route_to_indexed_witnesses():
    from gestaltdb.reasoning.executors import ExecutorRequest
    outcome = execute_closure(program(), facts(), collect_proofs=True,
                              executor_request=ExecutorRequest(mode="proofs"),
                              limits=ReasoningLimits())
    assert outcome.telemetry.executor == INDEXED_PYTHON
    assert outcome.telemetry.mode == "proofs" and outcome.telemetry.proofs
    assert outcome.result.proofs is not None


def test_generators_evaluate_once_with_shared_inputs():
    seen = iter([Fact("a", "edge", "b")])
    outcome = execute_closure(program(), seen, limits=ReasoningLimits())
    assert outcome.telemetry.input_facts == 1
    assert {f.predicate for f in outcome.result.facts} == {"edge", "reach"}
    with pytest.raises(ReasoningError, match="iterable"):
        execute_closure(program(), object(), limits=ReasoningLimits())


def unread():
    raise AssertionError("invalid invocation consumed input")
    yield


def test_unrelated_program_plan_rejected_before_input():
    other = Program([Rule("other", [Atom(X, "edge", Y)], Atom(X, "wrong", Y))])
    plan = PreparedProgram(other, CompilationIdentity(
        other.digest, CONTEXT, DICTIONARY, "positive-binary-entity-v1", INDEXED_SCC))
    with pytest.raises(ReasoningError, match="compilation"):
        execute_closure(program(), unread(), plan=plan, context=CONTEXT,
                        executor_request=ExecutorRequest(preferred="indexed-scc"), limits=ReasoningLimits())


@pytest.mark.parametrize("field,value", [
    ("program_digest", "other"), ("semantics_profile", "other"),
    ("dictionary_id", "other"), ("context", replace(CONTEXT, world="other")),
])
def test_generation_must_match_actual_invocation_before_input(field, value):
    with pytest.raises(ReasoningError, match="compilation"):
        execute_closure(program(), unread(), context=CONTEXT,
                        generation=replace(generation(), **{field: value}),
                        admission_request=AdmissionRequest(), limits=ReasoningLimits())


def test_admission_executor_must_match_dispatch_before_input():
    with pytest.raises(ReasoningError, match="executor"):
        execute_closure(program(), unread(), context=CONTEXT, generation=generation(),
                        admission_request=AdmissionRequest(executor=INDEXED_SCC), limits=ReasoningLimits())


def test_plan_context_and_dictionary_are_not_self_attested():
    for context, dictionary in [(replace(CONTEXT, world="other"), DICTIONARY), (CONTEXT, "other")]:
        plan = PreparedProgram(program(), CompilationIdentity(
            program().digest, context, dictionary, "positive-binary-entity-v1", INDEXED_SCC))
        with pytest.raises(ReasoningError, match="compilation"):
            execute_closure(program(), unread(), plan=plan, context=CONTEXT,
                            executor_request=ExecutorRequest(preferred="indexed-scc"), limits=ReasoningLimits())


def test_bound_execution_requires_independent_context():
    with pytest.raises(ReasoningError, match="InputContext"):
        execute_closure(program(), unread(), generation=generation(),
                        admission_request=AdmissionRequest(), limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="InputContext"):
        execute_closure(program(), unread(), plan=prepare(),
                        executor_request=ExecutorRequest(preferred="indexed-scc"), limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="indexed-scc"):
        execute_closure(program(), unread(), plan=prepare(), limits=ReasoningLimits())


def test_actual_missing_runtime_rejected_before_input(monkeypatch):
    def missing():
        raise ImportError("requires gestaltdb[polars]")
    monkeypatch.setattr("gestaltdb.reasoning.adapters.execution._polars", missing)
    with pytest.raises(ImportError, match="polars"):
        execute_closure(program(), unread(), context=CONTEXT,
                        executor_request=ExecutorRequest(preferred="polars-prototype", polars_available=True),
                        limits=ReasoningLimits())
