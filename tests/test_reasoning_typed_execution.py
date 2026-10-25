"""Typed capability admission and actual authenticated application execution."""

from dataclasses import replace

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.execution import execute_closure, execute_typed_closure
from gestaltdb.reasoning.adapters.session_generation import bind_session_generation, catalog_for_typed_program
from gestaltdb.reasoning.adapters.session_store import _digest, session_to_dict
from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session, advance_session
from gestaltdb.reasoning.adapters.typed_execution import execute_authenticated_session
from gestaltdb.reasoning.evidence_ledger import EvidenceLedger
from gestaltdb.reasoning.executors import ExecutorRequest, TYPED_PROFILE, TYPED_DICTIONARY, TYPED_INDEXED_PYTHON, select_executor
from gestaltdb.reasoning.generation import AdmissionRequest, GenerationIdentity, TemporalSourceIdentity, admit
from gestaltdb.reasoning.maintenance import build_index
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits, Variable, Program, Fact
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, GenericEngine, TypedAtom, TypedFact, Comparison

X, Y, Z = Variable("x"), Variable("y"), Variable("z")
REQUEST = ExecutorRequest(profile=TYPED_PROFILE)
PROGRAM = GenericProgram([
    GenericRule("r", [TypedAtom("memberOf", [X, Y])], [], TypedAtom("person", [X])),
    GenericRule("nary", [TypedAtom("memberOf", [X, Y])], [], TypedAtom("tag", [X, Y, Literal("integer", 1)])),
])


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    for agent in ("one", "two"):
        graph.assert_claim(subject="a", predicate="memberOf", object="g", agent=agent, polarity="positive", valid=(0, 100))
    session = start_session(graph, PROGRAM, catalog_for_typed_program(PROGRAM),
        scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
    binding = bind_session_generation(graph, session)
    yield graph, session, binding
    graph.close()


def pins(binding):
    return dict(expected_generation=binding.identity, expected_session_digest=binding.session_digest)


def test_raw_typed_literals_nary_filters_and_common_telemetry():
    program = GenericProgram([GenericRule("r", [TypedAtom("row", [X, Y, Z])],
        [Comparison(">", Z, Literal("integer", 1))], TypedAtom("pair", [X, Y]))])
    facts = [TypedFact("row", ["a", "b", Literal("integer", 2)]),
             TypedFact("row", ["a", "c", Literal("integer", 0)])]
    result = execute_typed_closure(program, iter(facts), limits=ReasoningLimits())
    assert set(result.result.facts) == set(GenericEngine().evaluate(program, facts, limits=ReasoningLimits()).facts)
    assert TypedFact("pair", ["a", "b"]) in result.result.facts
    assert result.telemetry.executor == TYPED_INDEXED_PYTHON
    assert result.telemetry.input_facts == 2 and result.telemetry.output_facts == 3
    assert result.telemetry.mode == "closure" and not result.telemetry.proofs
    assert result.telemetry.plan_digest is result.telemetry.admission_digest is None


@pytest.mark.parametrize("mode,preferred", [("proofs", "default"), ("closure", "indexed-scc"),
                                            ("closure", "polars-prototype")])
def test_typed_capabilities_fail_before_input_without_downgrade(mode, preferred):
    def forbidden():
        pytest.fail("consumed input before capability rejection")
        yield
    with pytest.raises(ReasoningError):
        execute_typed_closure(PROGRAM, forbidden(), limits=ReasoningLimits(),
            executor_request=ExecutorRequest(profile=TYPED_PROFILE, mode=mode, preferred=preferred, polars_available=True))


def test_binary_and_typed_entrypoints_do_not_implicitly_recode():
    def forbidden():
        pytest.fail("consumed input before profile rejection")
        yield
    with pytest.raises(ReasoningError, match="binary profile"):
        execute_closure(Program([]), forbidden(), limits=ReasoningLimits(), executor_request=REQUEST)
    with pytest.raises(ReasoningError, match="typed profile"):
        execute_typed_closure(PROGRAM, forbidden(), limits=ReasoningLimits(), executor_request=ExecutorRequest())
    with pytest.raises(ReasoningError, match="TypedFact"):
        execute_typed_closure(PROGRAM, [Fact("a", "memberOf", "g")], limits=ReasoningLimits())


def test_typed_generation_admission_exact_context_program_and_representation_before_input():
    context = InputContext("context", "default", PROGRAM.digest, "policy", "mapping")
    identity = GenerationIdentity(TemporalSourceIdentity("db", 1, "token", 50), context,
        PROGRAM.digest, "catalog", TYPED_PROFILE, TYPED_DICTIONARY)
    request = AdmissionRequest("closure", TYPED_INDEXED_PYTHON, TYPED_DICTIONARY)
    inputs = [TypedFact("memberOf", ["a", "g"])]
    outcome = execute_typed_closure(PROGRAM, inputs, limits=ReasoningLimits(), generation=identity,
        context=context, admission_request=request)
    assert outcome.telemetry.admission_digest == identity.digest
    assert admit(identity, request).generation_digest == identity.digest
    for mode in ("proofs", "maintenance-supports", "temporal-closure"):
        with pytest.raises(ReasoningError, match="typed indexed"):
            admit(identity, AdmissionRequest(mode, TYPED_INDEXED_PYTHON, TYPED_DICTIONARY))
    for field, value in (("program_digest", "wrong"), ("semantics_profile", "wrong"),
                         ("dictionary_id", "external-strings-v1"), ("context", replace(context, mapping_id="wrong"))):
        def forbidden():
            pytest.fail("consumed input before identity compatibility")
            yield
        with pytest.raises(ReasoningError):
            execute_typed_closure(PROGRAM, forbidden(), limits=ReasoningLimits(), generation=replace(identity, **{field: value}),
                context=context, admission_request=request)


def test_materialization_limits_cancellation_and_iterator_failures():
    facts = [TypedFact("memberOf", ["a", "g"])]
    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        execute_typed_closure(PROGRAM, facts * 2, limits=ReasoningLimits(max_input_facts=1))
    with pytest.raises(ReasoningLimitError, match="max_facts"):
        execute_typed_closure(PROGRAM, facts, limits=ReasoningLimits(max_facts=1))
    with pytest.raises(ReasoningCancelledError):
        execute_typed_closure(PROGRAM, facts, limits=ReasoningLimits(), cancel=lambda: True)
    def broken():
        yield facts[0]
        raise RuntimeError("iterator failed")
    with pytest.raises(RuntimeError, match="iterator failed"):
        execute_typed_closure(PROGRAM, broken(), limits=ReasoningLimits())
    assert execute_typed_closure(PROGRAM, facts, limits=ReasoningLimits()).result.facts


def test_authenticated_actual_base_execution_retains_independent_versions(source):
    graph, session, binding = source
    outcome = execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits())
    assert set(outcome.outcome.result.facts) == set(session.facts)
    assert outcome.binding == binding
    assert outcome.outcome.telemetry.admission_digest == binding.identity.digest
    assert outcome.outcome.telemetry.input_facts == 2  # versions, not unique base facts
    assert len(outcome.evidence) == 2 and len({v.fact for v in outcome.evidence}) == 1
    assert TypedFact("tag", ["a", "g", Literal("integer", 1)]) in outcome.outcome.result.facts
    with graph.read_view(valid_time=50) as view:
        assert view.provenance.commit_horizon == session.commit_horizon


def test_authenticated_fresh_recomputation_does_not_trust_supplied_closure(source):
    graph, session, binding = source
    forged_facts = (*session.facts, TypedFact("invented", ["z"]))
    forged = replace(session, facts=forged_facts, index=build_index(session.program, forged_facts, session.index.supports))
    rebound = bind_session_generation(graph, forged)  # source admission alone is deliberately not closure proof
    assert rebound.identity == binding.identity
    with pytest.raises(ReasoningError, match="differs from fresh"):
        execute_authenticated_session(graph, forged, **pins(rebound), limits=ReasoningLimits())


def test_missing_independent_version_is_rejected_before_execution(source, monkeypatch):
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    graph, session, binding = source
    kept = session.ledger.versions[:1]
    forged = replace(session, ledger=EvidenceLedger(session.ledger.scope_id, kept),
        version_logical=tuple(v for v in session.version_logical if v[0] == kept[0].version_id))
    def forbidden(*args, **kwargs):
        pytest.fail("engine ran before complete source admission")
    monkeypatch.setattr(adapter, "execute_typed_closure", forbidden)
    with pytest.raises(ProvenanceMismatchError, match="evidence"):
        execute_authenticated_session(graph, forged, expected_generation=binding.identity,
            expected_session_digest=_digest(session_to_dict(forged)), limits=ReasoningLimits())


@pytest.mark.parametrize("dimension", ["database_id", "commit_horizon", "valid_time_us", "provenance_token",
    "program_digest", "catalog_digest", "semantics_profile", "dictionary_id", "world", "ontology_revision",
    "source_policy_id", "mapping_id", "session_digest"])
def test_all_exact_session_pins_rejected_before_engine(source, monkeypatch, dimension):
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    graph, session, binding = source
    identity, kwargs = binding.identity, pins(binding)
    if dimension == "session_digest":
        kwargs["expected_session_digest"] = "sha256:" + "0" * 64
    elif dimension in identity.source.__dataclass_fields__:
        value = 999 if dimension in ("commit_horizon", "valid_time_us") else "other"
        kwargs["expected_generation"] = replace(identity, source=replace(identity.source, **{dimension: value}))
    elif dimension in identity.context.__dataclass_fields__:
        kwargs["expected_generation"] = replace(identity, context=replace(identity.context, **{dimension: "other"}))
    else:
        kwargs["expected_generation"] = replace(identity, **{dimension: "other"})
    def forbidden(*args, **kwargs):
        pytest.fail("engine ran before exact caller pin checks")
    monkeypatch.setattr(adapter, "execute_typed_closure", forbidden)
    with pytest.raises(ReasoningError):
        execute_authenticated_session(graph, session, **kwargs, limits=ReasoningLimits())


def test_latest_historical_and_new_generation_execution(source):
    graph, session, binding = source
    graph.assert_claim(subject="b", predicate="memberOf", object="g", agent="new", polarity="positive", valid=(0, 100))
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits())
    historical = execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits(), freshness="historical")
    assert TypedFact("person", ["b"]) not in historical.outcome.result.facts
    advanced = advance_session(graph, session, limits=ReasoningLimits()).session
    new = execute_authenticated_session(graph, advanced, **pins(bind_session_generation(graph, advanced)), limits=ReasoningLimits())
    assert TypedFact("person", ["b"]) in new.outcome.result.facts


def test_reentrant_source_move_during_evaluation_fails_before_return(source, monkeypatch):
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    graph, session, binding = source
    original = adapter.execute_typed_closure
    def moved(*args, **kwargs):
        outcome = original(*args, **kwargs)
        graph.assert_claim(subject="b", predicate="memberOf", object="g", agent="new", polarity="positive", valid=(0, 100))
        return outcome
    monkeypatch.setattr(adapter, "execute_typed_closure", moved)
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits())


def test_negative_and_aggregate_programs_require_their_separate_explicit_paths():
    from gestaltdb.reasoning.typed import AggregateRule, Aggregation
    negated = GenericProgram([GenericRule("not", [TypedAtom("p", [X])], [], TypedAtom("q", [X]),
                                          [TypedAtom("excluded", [X])])], closed=("excluded",))
    aggregate = GenericProgram([], [AggregateRule("n", [TypedAtom("p", [X])], [],
        [Aggregation("count", X, Y)], TypedAtom("count", [Y]))])
    for program in (negated, aggregate):
        def forbidden():
            pytest.fail("input consumed for unsupported typed program")
            yield
        with pytest.raises(ReasoningError, match="negation, aggregates"):
            execute_typed_closure(program, forbidden(), limits=ReasoningLimits())


def test_source_capability_rejection_precedes_source_admission(source, monkeypatch):
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    graph, session, binding = source
    def forbidden(*args, **kwargs):
        pytest.fail("source read before capability rejection")
    monkeypatch.setattr(adapter, "bind_session_generation", forbidden)
    for request in (ExecutorRequest(), ExecutorRequest(profile=TYPED_PROFILE, mode="proofs"),
                    ExecutorRequest(profile=TYPED_PROFILE, preferred="polars-prototype", polars_available=True)):
        with pytest.raises(ReasoningError):
            execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits(), executor_request=request)


def test_source_execution_bounds_cancellation_and_retired_versions(source):
    graph, session, binding = source
    with pytest.raises(ReasoningLimitError, match="max_input_facts"):
        execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits(max_input_facts=1))
    with pytest.raises(ReasoningCancelledError):
        execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits(), cancel=lambda: True)
    for version in tuple(graph.iter_claim_versions()):
        graph.retract_claim(version.logical_id, valid=(0, 100), supersedes_version_id=version.version_id)
    advanced = advance_session(graph, session, limits=ReasoningLimits()).session
    assert advanced.ledger.retired
    outcome = execute_authenticated_session(graph, advanced, **pins(bind_session_generation(graph, advanced)), limits=ReasoningLimits())
    assert outcome.evidence == () and outcome.outcome.telemetry.input_facts == 0
    assert outcome.outcome.result.facts == ()


def test_decoder_rebinding_after_engine_completion_is_rejected(source, monkeypatch):
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    from gestaltdb.serializers import JSONSerializer
    graph, session, binding = source
    original, serializer = adapter.execute_typed_closure, graph.serializer
    def rebound(*args, **kwargs):
        outcome = original(*args, **kwargs)
        graph.serializer = JSONSerializer()
        return outcome
    monkeypatch.setattr(adapter, "execute_typed_closure", rebound)
    try:
        with pytest.raises(ReasoningError, match="rebound"):
            execute_authenticated_session(graph, session, **pins(binding), limits=ReasoningLimits())
    finally:
        graph.serializer = serializer


def test_writer_cannot_move_source_during_authenticated_execution(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import gestaltdb.reasoning.adapters.typed_execution as adapter
    graph, session, binding = source
    entered, release, attempted, finished = Event(), Event(), Event(), Event()
    original = adapter.execute_typed_closure
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    def write():
        attempted.set()
        try:
            graph.assert_claim(subject="b", predicate="memberOf", object="g", agent="new", polarity="positive", valid=(0, 100))
        finally:
            finished.set()
    monkeypatch.setattr(adapter, "execute_typed_closure", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(execute_authenticated_session, graph, session, **pins(binding), limits=ReasoningLimits())
        try:
            assert entered.wait(5)
            writer = pool.submit(write)
            assert attempted.wait(5) and not finished.wait(0.05)
        finally:
            release.set()
        outcome = executing.result(timeout=5)
        writer.result(timeout=5)
    assert TypedFact("person", ["b"]) not in outcome.outcome.result.facts
    assert finished.is_set()  # Guarantee ends at return, not a continuing latest lease.


def test_typed_execution_documentation_examples():
    from pathlib import Path
    import re
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-typed-execution.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 2
    for block in blocks:
        exec("\n".join(line[3:] if line.startswith("   ") else line for line in block.splitlines()), {})
