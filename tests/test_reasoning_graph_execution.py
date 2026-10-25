"""Mutable-source admission never impersonates a temporal generation."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ontology import Ontology, SubPropertyOf, Transitive, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning import Fact, ReasoningError, ReasoningLimits, ReasoningLimitError
from gestaltdb.reasoning.adapters.generation import catalog_for_ontology
from gestaltdb.reasoning.adapters.graph import GraphMapping, GraphSourceLimits, PinnedGraphSource, _graph_context
from gestaltdb.reasoning.adapters.graph_execution import CurrentGenerationBinding, bind_current_generation, execute_current_graph
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.executors import ExecutorRequest, INDEXED_PYTHON
from gestaltdb.reasoning.generation import CurrentSourceIdentity, GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.model import ReasoningCancelledError
from gestaltdb.reasoning.plans import PreparedProgram
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import CompilationIdentity


MAPPING = GraphMapping(edge_types=(("KNOWS", "edge"),))


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.put_nodes([Node("a"), Node("b"), Node("c")])
    graph.put_edges_bulk([Edge("ab", "a", "b", {"type": "KNOWS"}),
                          Edge("ab2", "a", "b", {"type": "KNOWS"}),
                          Edge("bc", "b", "c", {"type": "KNOWS"})])
    yield graph
    graph.close()


def compiled():
    return compile_ontology(Ontology("test", [SubPropertyOf("edge", "reach"), Transitive("reach")]))


def prepare(context):
    return PreparedProgram(compiled().program, CompilationIdentity(
        compiled().program.digest, context, "external-strings-v1", "positive-binary-entity-v1", "indexed-scc-v1"))


def run(view, *, ontology=None, catalog=None, **kwargs):
    ontology = compiled() if ontology is None else ontology
    return execute_current_graph(view, ontology, catalog_for_ontology(ontology) if catalog is None else catalog,
                                 mapping=kwargs.pop("mapping", MAPPING),
                                 limits=kwargs.pop("limits", ReasoningLimits()), **kwargs)


def test_default_complete_projection_binding_and_no_writes(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        result = run(view)
        assert result.outcome.telemetry.executor == INDEXED_PYTHON
        assert result.outcome.telemetry.input_facts == 3
        assert result.outcome.telemetry.admission_digest == result.binding.identity.digest
        assert Fact("a", "reach", "c") in result.outcome.result.facts
        assert isinstance(result.binding.identity.source, CurrentSourceIdentity)
        result.binding.verify_view(view)
        result.binding.verify_source(graph)
        assert run(view, expected_generation=result.binding.identity).outcome.result.facts == result.outcome.result.facts
        with PinnedGraphSource(view, mapping=MAPPING) as source:
            assert result.outcome.result.facts == source.evaluate(compiled(), limits=ReasoningLimits()).result.facts
    result.binding.verify_source(graph)
    with pytest.raises(RuntimeError):
        result.binding.verify_view(view)


@pytest.mark.parametrize("preferred", ["default", "indexed-scc", "polars-prototype"])
def test_executor_parity_and_prepared_plan(graph, preferred):
    if preferred == "polars-prototype":
        pytest.importorskip("polars")
    with graph.current_read_view(require_verifiable=True) as view:
        binding = bind_current_generation(view, compiled(), catalog_for_ontology(compiled()), mapping=MAPPING)
        context = binding.identity.context
        plan = prepare(context) if preferred == "indexed-scc" else None
        outcome = run(view, plan=plan, executor_request=ExecutorRequest(
            preferred=preferred, polars_available=preferred == "polars-prototype"))
        assert outcome.outcome.result.facts == run(view).outcome.result.facts
        assert outcome.outcome.telemetry.admission_digest == outcome.binding.identity.digest


def test_explicit_polars_budget_through_authenticated_current_consumer(graph):
    pytest.importorskip("polars")
    request = ExecutorRequest(preferred="polars-prototype", polars_available=True)
    with graph.current_read_view(require_verifiable=True) as view:
        reference = run(view)
        with pytest.raises(ReasoningLimitError, match="max_intermediate_rows"):
            run(view, executor_request=request, polars_max_intermediate_rows=1)
        result = run(view, executor_request=request, polars_max_intermediate_rows=1000,
                     expected_generation=reference.binding.identity)
        assert result.binding.identity == reference.binding.identity
        assert result.outcome.result.facts == reference.outcome.result.facts
        assert result.outcome.polars_run.metrics["max_intermediate_rows"] == 1000


@pytest.mark.parametrize("value", [1, False, 0, "100"])
def test_current_consumer_rejects_irrelevant_or_invalid_budget_before_projection(graph, monkeypatch, value):
    with graph.current_read_view(require_verifiable=True) as view:
        def forbidden(*args):
            pytest.fail("records read before physical option rejection")
        monkeypatch.setattr(view, "get_node", forbidden)
        monkeypatch.setattr(view, "get_edge", forbidden)
        with pytest.raises(ReasoningError, match="polars_max_intermediate_rows"):
            run(view, polars_max_intermediate_rows=value)


@pytest.mark.parametrize("preferred", ["default", "indexed-scc"])
def test_exact_parallel_edge_evidence_in_proofs(graph, preferred):
    with graph.current_read_view(require_verifiable=True) as view:
        context = _graph_context(view.provenance, compiled().revision_id, MAPPING)
        plan = prepare(context) if preferred == "indexed-scc" else None
        result = run(view, plan=plan, collect_proofs=True,
                     executor_request=ExecutorRequest(preferred=preferred, mode="proofs"))
        with PinnedGraphSource(view, mapping=MAPPING) as source:
            refs = {e.reference_id for e in source._evidence() if e.fact == Fact("a", "edge", "b")}
        node = next(n for n in result.outcome.result.proofs.nodes if n.fact == Fact("a", "edge", "b"))
        assert node.evidence == (min(refs),) and len(refs) == 2  # bounded witness, not all-support provenance


@pytest.mark.parametrize("dimension", ["database", "sequence", "token", "context_id", "world", "ontology_revision",
                                        "source_policy_id", "mapping_id", "program_digest", "catalog_digest",
                                        "semantics_profile", "dictionary_id", "temporal"])
def test_every_expected_dimension_rejected_before_record_reads(graph, monkeypatch, dimension):
    with graph.current_read_view(require_verifiable=True) as view:
        identity = run(view).binding.identity
        if dimension in ("database", "sequence", "token"):
            field, value = {"database": ("database_id", "other"), "sequence": ("snapshot_sequence", 99999),
                            "token": ("provenance_token", "other")}[dimension]
            wrong = replace(identity, source=replace(identity.source, **{field: value}))
        elif dimension == "temporal":
            wrong = replace(identity, source=TemporalSourceIdentity(identity.source.database_id,
                            identity.source.snapshot_sequence, identity.source.provenance_token, 0))
        elif dimension in identity.context.__dataclass_fields__:
            wrong = replace(identity, context=replace(identity.context, **{dimension: "other"}))
        else:
            wrong = replace(identity, **{dimension: "other"})
        assert wrong.digest != identity.digest
        def forbidden(*args):
            pytest.fail("record read before admission")
        monkeypatch.setattr(view, "get_node", forbidden)
        monkeypatch.setattr(view, "get_edge", forbidden)
        with pytest.raises(ReasoningError, match="drifted"):
            run(view, expected_generation=wrong)


def test_catalog_content_and_version_binding(graph):
    catalog = catalog_for_ontology(compiled())
    with graph.current_read_view(require_verifiable=True) as view:
        first = run(view)
        bad = RuleCatalog([replace(e, content_digest="sha256:bad") for e in catalog.entries])
        with pytest.raises(ReasoningError, match="catalog content"):
            run(view, catalog=bad)
        with pytest.raises(ReasoningError, match="catalog content"):
            run(view, catalog=RuleCatalog([]))
        versioned = RuleCatalog([replace(e, version_id="new", ordinal=2) for e in catalog.entries])
        second = run(view, catalog=versioned)
        assert second.binding.identity.catalog_digest != first.binding.identity.catalog_digest
        with pytest.raises(ReasoningError, match="catalog_digest"):
            run(view, catalog=versioned, expected_generation=first.binding.identity)


def test_live_source_movement_does_not_change_old_view(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        first = run(view)
        graph.put_edge(Edge("cd", "c", "d", {"type": "KNOWS"}))
        first.binding.verify_view(view)
        assert run(view, expected_generation=first.binding.identity).outcome.result.facts == first.outcome.result.facts
        with pytest.raises(ProvenanceMismatchError):
            first.binding.verify_source(graph)
    with graph.current_read_view(require_verifiable=True) as latest:
        with pytest.raises(ReasoningError, match="source"):
            run(latest, expected_generation=first.binding.identity)
        assert Fact("a", "reach", "d") in run(latest).outcome.result.facts


def test_bounds_cancellation_even_without_projected_facts(graph, monkeypatch):
    with graph.current_read_view(require_verifiable=True) as view:
        with pytest.raises(ReasoningLimitError, match="max_records"):
            run(view, source_limits=GraphSourceLimits(1))
        with pytest.raises(ReasoningLimitError, match="max_input_facts"):
            run(view, limits=ReasoningLimits(max_input_facts=1))
        reads = []
        original = view.get_node
        def counted(key):
            reads.append(key)
            return original(key)
        monkeypatch.setattr(view, "get_node", counted)
        with pytest.raises(ReasoningCancelledError):
            run(view, mapping=GraphMapping(), cancel=lambda: len(reads) >= 2)
        assert len(reads) == 2
        assert run(view).outcome.result.facts


def test_rebound_and_forged_views_rejected(graph, monkeypatch):
    with graph.current_read_view(require_verifiable=True) as view:
        provenance = view.provenance
        view.provenance = replace(provenance, state_sha256="0" * 64)
        with pytest.raises(ProvenanceMismatchError, match="digest"):
            run(view)
        view.provenance = provenance
        original = view.get_edge
        def rebound(key):
            edge = original(key)
            view._graph = graph
            return edge
        monkeypatch.setattr(view, "get_edge", rebound)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            run(view)


@pytest.mark.parametrize("field,value", [("backend_name", "leveldb"), ("backend_layout_version", 99),
                                         ("serializer_name", "pickle"), ("serializer_format_version", 99)])
def test_backend_and_serializer_metadata_authenticated_independently(graph, field, value):
    with graph.current_read_view(require_verifiable=True) as view:
        view.provenance = replace(view.provenance, **{field: value})
        with pytest.raises(ProvenanceMismatchError, match="backend/serializer"):
            run(view)


def test_serializer_rebinding_rejected_after_read(graph, monkeypatch):
    from gestaltdb.serializers import JSONSerializer
    with graph.current_read_view(require_verifiable=True) as view:
        original = view.get_node
        def replaced(key):
            node = original(key)
            view._graph.serializer = JSONSerializer()
            return node
        monkeypatch.setattr(view, "get_node", replaced)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            run(view)


def test_complete_projection_does_not_use_stale_indexes(graph):
    graph._mark_indexes_stale("node_label", "node_property", "edge_type")
    with graph.current_read_view(require_verifiable=True) as view:
        assert Fact("a", "reach", "c") in run(view).outcome.result.facts


def test_engine_completion_does_not_bypass_source_or_cancellation_checks(graph, monkeypatch):
    import gestaltdb.reasoning.adapters.graph_execution as adapter
    original = adapter.execute_closure
    with graph.current_read_view(require_verifiable=True) as view:
        def rebound(*args, **kwargs):
            outcome = original(*args, **kwargs)
            view._graph = graph
            return outcome
        monkeypatch.setattr(adapter, "execute_closure", rebound)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            run(view)


def test_binding_constructor_rejects_policy_and_temporal_drift(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        binding = bind_current_generation(view, compiled(), catalog_for_ontology(compiled()), mapping=MAPPING)
        for changed in (replace(binding.identity, context=replace(binding.identity.context, world="other")),
                        replace(binding.identity, dictionary_id="other"),
                        replace(binding.identity, semantics_profile="other"),
                        replace(binding.identity, source=TemporalSourceIdentity("db", 1, "token", 0))):
            with pytest.raises(ProvenanceMismatchError, match="binding mismatch"):
                replace(binding, identity=changed)
        with pytest.raises(ProvenanceMismatchError):
            replace(binding, mapping=GraphMapping())
        with pytest.raises(ReasoningError, match="GraphMapping"):
            CurrentGenerationBinding(binding.identity, binding.provenance, None)


def test_invalid_values_and_temporal_or_partial_inputs_are_not_admitted(graph):
    graph.put_node(Node("bad", properties={"entity": 42}))
    with graph.current_read_view(require_verifiable=True) as view:
        with pytest.raises(ReasoningError, match="mapped entity property"):
            run(view, mapping=GraphMapping(node_properties=(("entity", "p"),)))
        with pytest.raises(ReasoningError, match="CompiledOntology"):
            execute_current_graph(view, object(), catalog_for_ontology(compiled()), mapping=MAPPING, limits=ReasoningLimits())
    with graph.read_view(valid_time=50) as temporal:
        with pytest.raises(ReasoningError, match="CurrentGraphReadView"):
            run(temporal)


def test_leveldb_never_falls_back(tmp_path):
    pytest.importorskip("plyvel")
    graph = GraphDB.create(tmp_path / "leveldb", backend="leveldb", serializer="json")
    try:
        with pytest.raises(NotImplementedError):
            with graph.current_read_view(require_verifiable=True) as view:
                run(view)
    finally:
        graph.close()


def test_wrong_plan_mode_and_backend_fail_closed(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        wrong = prepare(InputContext("wrong", "w", "r", "p", "m"))
        with pytest.raises(ReasoningError, match="compilation identity"):
            run(view, plan=wrong, executor_request=ExecutorRequest(preferred="indexed-scc"))
        with pytest.raises(ReasoningError, match="witness"):
            run(view, collect_proofs=True, executor_request=ExecutorRequest(
                mode="proofs", preferred="polars-prototype", polars_available=True))
    with graph.current_read_view(capture_provenance=False) as view:
        with pytest.raises(ReasoningError, match="verifiable"):
            run(view)


def test_temporal_digest_unchanged_and_source_kind_domain_separated():
    context = InputContext("context", "base", "revision", "policy", "mapping")
    temporal = GenerationIdentity(TemporalSourceIdentity("db", 1, "token", 50), context,
                                  "program", "catalog", "positive", "dictionary")
    payload = ["gestaltdb-generation-identity-v1", ["db", 1, "token", 50],
               ["context", "base", "revision", "policy", "mapping"],
               "program", "catalog", "positive", "dictionary"]
    expected = "sha256:" + hashlib.sha256(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    assert temporal.digest == expected
    current = replace(temporal, source=CurrentSourceIdentity("db", 1, "token"))
    assert current.digest != temporal.digest
    with pytest.raises(ReasoningError, match="source"):
        temporal.require_compatible(current)


@pytest.mark.parametrize("sequence", [True, -1, 1.5, "1"])
def test_invalid_current_sequences(sequence):
    with pytest.raises(ReasoningError, match="snapshot_sequence"):
        CurrentSourceIdentity("db", sequence, "token")


def test_current_admission_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-current-admission.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})
