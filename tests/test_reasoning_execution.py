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
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError
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
    assert indexed.polars_run is None
    assert outcome.polars_run.result is outcome.result
    assert outcome.polars_run.metrics["max_intermediate_rows"] == 100_000


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", {}])
def test_invalid_physical_budget_rejected_before_input(value):
    def forbidden():
        pytest.fail("input consumed before option validation")
        yield
    with pytest.raises(ReasoningError, match="positive integer"):
        execute_closure(program(), forbidden(), context=CONTEXT, limits=ReasoningLimits(),
                        executor_request=ExecutorRequest(preferred="polars-prototype", polars_available=True),
                        polars_max_intermediate_rows=value)


@pytest.mark.parametrize("preferred", ["default", "indexed-python", "indexed-scc"])
def test_polars_option_cannot_silently_change_or_apply_to_indexed_executor(preferred):
    def forbidden():
        pytest.fail("input consumed before irrelevant option rejection")
        yield
    with pytest.raises(ReasoningError, match="requires explicit polars"):
        execute_closure(program(), forbidden(), context=CONTEXT, limits=ReasoningLimits(),
                        executor_request=ExecutorRequest(preferred=preferred), polars_max_intermediate_rows=1000)


def test_physical_budget_does_not_change_logical_generation_or_weaken_other_limits():
    pytest.importorskip("polars")
    request = ExecutorRequest(preferred="polars-prototype", polars_available=True)
    options = dict(executor_request=request, context=CONTEXT, generation=generation(),
                   admission_request=AdmissionRequest(executor=POLARS_PROTOTYPE))
    for budget in (10, 1000):
        outcome = execute_closure(program(), facts(), limits=ReasoningLimits(),
                                  polars_max_intermediate_rows=budget, **options)
        assert outcome.telemetry.admission_digest == generation().digest
        assert outcome.polars_run.metrics["max_intermediate_rows"] == budget
        assert outcome.polars_run.metrics["peak_intermediate_rows"] <= budget
        assert outcome.polars_run.metrics["candidate_rows"] > 0
    with pytest.raises(ReasoningLimitError, match="max_intermediate_rows"):
        execute_closure(program(), facts(), limits=ReasoningLimits(), polars_max_intermediate_rows=1, **options)
    with pytest.raises(ReasoningLimitError, match="max_candidate_rows"):
        execute_closure(program(), facts(), limits=ReasoningLimits(max_candidate_rows=1),
                        polars_max_intermediate_rows=1000, **options)
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        execute_closure(program(), facts(), limits=ReasoningLimits(max_facts=3),
                        polars_max_intermediate_rows=1000, **options)


def test_explicit_dispatcher_budget_admits_over_100k_rows_with_complete_reference_parity():
    pytest.importorskip("polars")
    count = 100_001
    inputs = [Fact(f"n{i}", "edge", "target") for i in range(count)]
    request = ExecutorRequest(preferred="polars-prototype", polars_available=True)
    limits = ReasoningLimits(max_input_facts=110_000, max_facts=220_000, max_candidate_rows=5_000_000)
    with pytest.raises(ReasoningLimitError, match="max_intermediate_rows"):
        execute_closure(program(), inputs, context=CONTEXT, executor_request=request, limits=limits)
    result = execute_closure(program(), inputs, context=CONTEXT, executor_request=request,
                             polars_max_intermediate_rows=220_000, limits=limits)
    reference = execute_closure(program(), inputs, limits=limits)
    assert set(result.result.facts) == set(reference.result.facts)
    assert len(result.result.facts) == count * 2
    assert result.polars_run.metrics["peak_intermediate_rows"] > 100_000
    assert result.polars_run.metrics["max_intermediate_rows"] == 220_000


def test_executor_documentation_examples():
    from pathlib import Path
    import re
    pytest.importorskip("polars")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-executors.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 2
    for block in blocks:
        exec("\n".join(line[3:] if line.startswith("   ") else line for line in block.splitlines()), {})


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


@pytest.mark.parametrize("preferred", ["default", "indexed-scc", "polars-prototype"])
def test_input_consumption_stops_at_bound_plus_one(preferred):
    from itertools import count
    from gestaltdb.reasoning.model import ReasoningLimitError

    if preferred == "polars-prototype":
        pytest.importorskip("polars")
    seen = []
    def endless_duplicates():
        for i in count():
            seen.append(i)
            if i > 2:
                raise AssertionError("read beyond input bound plus one")
            yield Fact("a", "edge", "b")
    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        execute_closure(program(), endless_duplicates(), limits=ReasoningLimits(max_input_facts=2),
                        context=CONTEXT, plan=prepare() if preferred == "indexed-scc" else None,
                        executor_request=ExecutorRequest(preferred=preferred, polars_available=True))
    assert seen == [0, 1, 2]


def test_cancellation_during_input_consumption():
    from gestaltdb.reasoning.model import ReasoningCancelledError

    seen = []
    def source():
        for i in range(100):
            seen.append(i)
            yield Fact("a", "edge", "b")
    with pytest.raises(ReasoningCancelledError):
        execute_closure(program(), source(), limits=ReasoningLimits(), cancel=lambda: len(seen) == 2)
    assert seen == [0, 1]


def test_exact_input_bound_and_iterator_failures():
    result = execute_closure(program(), iter(facts()), limits=ReasoningLimits(max_input_facts=3))
    assert result.telemetry.input_facts == 3
    def broken():
        yield Fact("a", "edge", "b")
        raise TypeError("source failed")
    with pytest.raises(TypeError, match="source failed"):
        execute_closure(program(), broken(), limits=ReasoningLimits())
    with pytest.raises(ReasoningError, match="Fact or Evidence"):
        execute_closure(program(), [None], limits=ReasoningLimits())


@pytest.mark.parametrize("preferred", ["default", "indexed-scc"])
@pytest.mark.parametrize("first", ["claim-version-123", "claim-version-456"])
def test_exact_independent_evidence_matches_direct_proofs(preferred, first):
    from gestaltdb.reasoning.engine import DatalogEngine
    from gestaltdb.reasoning.plans import PlannedDatalogEngine
    from gestaltdb.reasoning.protocols import Evidence

    edge = Fact("a", "edge", "b")
    evidence = [Evidence(edge, first), Evidence(edge, "claim-version-456"),
                Evidence(edge, "claim-version-123"), Fact("b", "edge", "c")]
    plan = prepare() if preferred == "indexed-scc" else None
    if plan is None:
        direct = DatalogEngine().evaluate(program(), evidence, limits=ReasoningLimits(), collect_proofs=True)
    else:
        direct = PlannedDatalogEngine().evaluate(
            plan, evidence, identity=plan.identity, limits=ReasoningLimits(), collect_proofs=True).result
    dispatched = execute_closure(program(), iter(evidence), context=CONTEXT, plan=plan,
                                  executor_request=ExecutorRequest(preferred=preferred, mode="proofs"),
                                  collect_proofs=True, limits=ReasoningLimits())
    assert dispatched.result == direct
    assert dispatched.telemetry.input_facts == len(evidence)
    node = next(node for node in dispatched.result.proofs.nodes if node.fact == edge)
    # These engines select one bounded witness, not an all-support ledger.
    assert node.evidence == ("claim-version-123",)


def test_evidence_closure_parity_with_polars():
    pytest.importorskip("polars")
    from gestaltdb.reasoning.protocols import Evidence
    evidence = [Evidence(Fact("a", "edge", "b"), "claim-version-123")]
    indexed = execute_closure(program(), evidence, limits=ReasoningLimits())
    polars = execute_closure(program(), evidence, context=CONTEXT, limits=ReasoningLimits(),
                             executor_request=ExecutorRequest(preferred="polars-prototype", polars_available=True))
    assert set(indexed.result.facts) == set(polars.result.facts)
    assert polars.result.proofs is None
