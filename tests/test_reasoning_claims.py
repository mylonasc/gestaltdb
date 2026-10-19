"""Read-only temporal integration: the adapter, not the kernel, owns context."""

import copy
from dataclasses import FrozenInstanceError, replace
import importlib.util
from pathlib import Path
import textwrap

import pytest

from gestaltdb.graphdb import GraphDB, _DATABASE_ID_METADATA_KEY
from gestaltdb.ingestion import IndexMaintenanceMode
from gestaltdb.ontology.compiler import compile_ontology
from gestaltdb.ontology.model import Domain, Ontology, SubClassOf, TYPE_PREDICATE
from gestaltdb.readview import GraphReadView, ProvenanceMismatchError
from gestaltdb.reasoning.adapters import claims as adapter
from gestaltdb.reasoning.adapters.claims import (
    ClaimMapping, ClaimReasoningResult, ClaimSelection, ClaimSourceLimits, PinnedClaimSource,
)
from gestaltdb.reasoning.model import Fact, ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInstant, TemporalInterval
from gestaltdb.versioning import VersionOperation
from tests.test_temporal_as_of import _MetadataStore


@pytest.fixture
def graph(tmp_path):
    graph = GraphDB(_MetadataStore(), JSONSerializer())
    graph._backend_name = "leveldb"
    graph.save_manifest(tmp_path / "managed")
    try:
        yield graph
    finally:
        graph.close()


def _claim(graph, subject="alice", predicate="worksFor", object="acme", **kwargs):
    values = dict(subject=subject, predicate=predicate, object=object, agent="registry",
                  polarity="positive", world="default", valid=(0, 100))
    values.update(kwargs)
    return graph.assert_claim(**values)


def _compiled(name="organization"):
    return compile_ontology(Ontology(name, [Domain("worksFor", "Researcher"), SubClassOf("Researcher", "Person")]))


def _source(view, compiled=None, **kwargs):
    values = dict(world="default", ontology_revision=(compiled or _compiled()).revision_id,
                  selection=ClaimSelection(), mapping=ClaimMapping())
    values.update(kwargs)
    return PinnedClaimSource(view, **values)


def test_read_only_complete_result_retains_exact_input_context_and_evidence(graph, monkeypatch):
    original = _claim(graph, confidence=0.0)
    compiled = _compiled()
    with graph.read_view(valid_time=50) as view:
        before = copy.deepcopy((graph.store.metadata, graph.store.indexes, graph.store.range_indexes))

        def forbidden(*args, **kwargs):
            raise AssertionError("adapter must not write or read current graph records")

        with monkeypatch.context() as patch:
            for name in ("put_metadata", "delete_metadata", "put_index_entry", "delete_index_entry",
                         "put_range_index_entry", "delete_range_index_entry"):
                patch.setattr(graph.store, name, forbidden)
            patch.setattr(graph, "get_node", forbidden)
            patch.setattr(graph, "get_edge", forbidden)
            with _source(view, compiled) as source:
                result = source.evaluate(compiled, limits=ReasoningLimits(), collect_proofs=True)
                assert source.context == result.context
                assert result.provenance == view.provenance
        assert before == (graph.store.metadata, graph.store.indexes, graph.store.range_indexes)
    root = Fact("alice", TYPE_PREDICATE, "Person")
    assert root in result
    assert result.match(subject="alice", predicate=TYPE_PREDICATE)
    assert result.context.ontology_revision == compiled.revision_id
    assert result.provenance.valid_time_us == 50
    assert not result.explain(root).truncated
    leaf = next(node for node in result.explain(root).nodes if node.rule is None)
    assert leaf.evidence == (original.version_id,)
    result.verify_source(graph)


def test_pin_survives_later_corrections_and_new_view_has_a_different_context(graph):
    first = _claim(graph)
    compiled = _compiled()
    with graph.read_view(valid_time=50) as old_view, _source(old_view) as source:
        first_result = source.evaluate(compiled, limits=ReasoningLimits())
        corrected = graph.correct_claim(first.logical_id, confidence=0.9, supersedes_version_id=first.version_id)
        assert source.evaluate(compiled, limits=ReasoningLimits()) == first_result
        assert next(source.iter_evidence()).reference_id == first.version_id
        with graph.read_view(valid_time=50) as new_view, _source(new_view) as new_source:
            result = new_source.evaluate(compiled, limits=ReasoningLimits())
            assert result.context != first_result.context
            assert result.facts == first_result.facts
            assert next(new_source.iter_evidence()).reference_id == corrected.version_id
    first_result.verify_source(graph)


def test_positive_entity_half_open_point_selection_and_negative_coexistence(graph):
    _claim(graph)
    _claim(graph, polarity="negative", agent="denier")
    _claim(graph, subject="expired", valid=(0, 50))
    _claim(graph, subject="starts-now", valid=(50, 100))
    _claim(graph, subject="literal", object="acme", object_kind="literal")
    _claim(graph, subject="other-world", world="alternate")
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        facts = {evidence.fact for evidence in source.iter_evidence()}
        assert facts == {Fact("alice", "worksFor", "acme"), Fact("starts-now", "worksFor", "acme")}


def test_explicit_source_agent_policy_distinguishes_all_none_and_unsourced(graph):
    _claim(graph, subject="a", agent="one", source=None)
    _claim(graph, subject="b", agent="one", source="file")
    _claim(graph, subject="c", agent="two", source="file")
    with graph.read_view(valid_time=50) as view:
        contexts = []
        for selection, subjects in [
            (ClaimSelection(), {"a", "b", "c"}),
            (ClaimSelection(sources=[None]), {"a"}),
            (ClaimSelection(agents=["one"], sources=["file"]), {"b"}),
            (ClaimSelection(agents=[]), set()),
            (ClaimSelection(sources=[]), set()),
        ]:
            with _source(view, selection=selection) as source:
                assert {item.fact.subject for item in source.iter_evidence()} == subjects
                contexts.append(source.context)
        assert len(set(contexts)) == 5


def test_explicit_mapping_is_canonical_and_part_of_context_identity(graph):
    _claim(graph, predicate="IS_A", object="Researcher")
    compiled = _compiled()
    mapping = ClaimMapping([["IS_A", TYPE_PREDICATE], ["EMPLOYED", "worksFor"]])
    assert mapping == ClaimMapping(list(reversed(mapping.predicate_aliases)))
    assert mapping.map(Fact("?s", "unknown", "?o")) == Fact("?s", "unknown", "?o")
    with graph.read_view(valid_time=50) as view, _source(view, mapping=mapping) as source:
        result = source.evaluate(compiled, limits=ReasoningLimits())
        assert Fact("alice", TYPE_PREDICATE, "Person") in result
        with _source(view) as identity_source:
            assert source.context != identity_source.context
            identity_result = identity_source.evaluate(compiled, limits=ReasoningLimits())
            assert Fact("alice", TYPE_PREDICATE, "Person") not in identity_result


def test_actual_rule_outputs_excluded_unless_opted_in_and_policy_fingerprint_changes(graph):
    _claim(graph, predicate="employment")
    graph.create_rule("employment", when=[("?x", "employment", "?y")], then=("?x", "worksFor", "?y"))
    graph.run_rules(as_of=50)
    _claim(graph, subject="ontology-derived", agent="gestaltdb:ontology", provenance={"derived_by": "gestaltdb.ontology"})
    # Arbitrary provenance JSON is legal and is not a recognized derivation marker.
    _claim(graph, subject="external", agent="gestaltdb:rules", provenance={"derived_by": {"custom": True}})
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        ordinary = {item.fact for item in source.iter_evidence()}
        assert Fact("alice", "worksFor", "acme") not in ordinary
        assert Fact("ontology-derived", "worksFor", "acme") not in ordinary
        assert Fact("external", "worksFor", "acme") in ordinary
        with _source(view, selection=ClaimSelection(include_derived=True)) as opted_in:
            all_facts = {item.fact for item in opted_in.iter_evidence()}
            assert Fact("alice", "worksFor", "acme") in all_facts
            assert Fact("ontology-derived", "worksFor", "acme") in all_facts
            assert opted_in.context.source_policy_id != source.context.source_policy_id


def test_duplicate_evidence_preserves_versions_but_not_logical_multiplicity(graph):
    first = _claim(graph, agent="one")
    second = _claim(graph, agent="two")
    compiled = _compiled()
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        assert {item.reference_id for item in source.iter_evidence()} == {first.version_id, second.version_id}
        result = source.evaluate(compiled, limits=ReasoningLimits(), collect_proofs=True)
        leaves = [node for node in result.result.proofs.nodes if node.rule is None]
        assert len(leaves) == 1
        assert leaves[0].evidence == (min(first.version_id, second.version_id),)
        with pytest.raises(ReasoningLimitError, match="max_input_facts"):
            source.evaluate(compiled, limits=ReasoningLimits(max_input_facts=1))


def test_source_scan_counter_bounds_rejected_candidates_without_successful_partial_result(graph, monkeypatch):
    for name in "abcd":
        _claim(graph, subject=name)
    seen = []
    original = graph.iter_claims_as_of

    def observed(**kwargs):
        for version in original(**kwargs):
            seen.append(version.version_id)
            yield version

    monkeypatch.setattr(graph, "iter_claims_as_of", observed)
    with graph.read_view(valid_time=50) as view, _source(
        view, selection=ClaimSelection(agents=["absent"]), limits=ClaimSourceLimits(2),
    ) as source:
        with pytest.raises(ReasoningLimitError, match="max_scanned_claims"):
            source.evaluate(_compiled(), limits=ReasoningLimits())
    assert len(seen) == 3


@pytest.mark.parametrize("field", ["time", "provenance", "graph"])
def test_mutable_view_rebinding_is_rejected(graph, field):
    _claim(graph)
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        if field == "time":
            view.valid_time = TemporalInstant(51)
        elif field == "provenance":
            view.provenance = replace(view.provenance, valid_time_us=51)
        else:
            view._graph = object()
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            source.evaluate(_compiled(), limits=ReasoningLimits())


def test_rebinding_during_backend_iteration_is_caught_before_yield(graph, monkeypatch):
    _claim(graph)
    original = graph.iter_claims_as_of
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        def rebound(**kwargs):
            for version in original(**kwargs):
                view.valid_time = TemporalInstant(51)
                yield version

        monkeypatch.setattr(graph, "iter_claims_as_of", rebound)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            source.evaluate(_compiled(), limits=ReasoningLimits())


def test_verification_after_inference_rejects_view_mutation(graph, monkeypatch):
    _claim(graph)
    original = adapter.DatalogEngine.evaluate
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            view.valid_time = TemporalInstant(51)
            return result

        monkeypatch.setattr(adapter.DatalogEngine, "evaluate", changed)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            source.evaluate(_compiled(), limits=ReasoningLimits())


def test_corruption_at_stream_exhaustion_is_rejected(graph, monkeypatch):
    _claim(graph)
    original = graph.iter_claims_as_of
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        def corrupted(**kwargs):
            yield from original(**kwargs)
            graph.store.metadata.pop(graph._temporal_visible_key(view.commit_horizon))

        monkeypatch.setattr(graph, "iter_claims_as_of", corrupted)
        with pytest.raises(ValueError):
            source.evaluate(_compiled(), limits=ReasoningLimits())


@pytest.mark.parametrize("change", ["commit", "zero-commit", "logical-id", "version-id", "operation",
                                    "valid", "world", "polarity", "literal", "invalid"])
def test_invalid_or_out_of_context_backend_records_fail_closed(graph, monkeypatch, change):
    version = _claim(graph)
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        bad = version
        if change == "commit":
            bad = replace(version, commit_id=view.commit_horizon + 1)
        elif change == "zero-commit":
            bad = replace(version, commit_id=0)
        elif change == "logical-id":
            bad = replace(version, logical_id="different-claim")
        elif change == "version-id":
            bad = replace(version, version_id=None)
        elif change == "operation":
            bad = replace(version, operation=VersionOperation.RETRACT)
        elif change == "valid":
            bad = replace(version, valid=TemporalInterval(TemporalInstant(51), None))
        elif change == "world":
            bad = replace(version, claim=replace(version.claim, world="other"))
        elif change == "polarity":
            bad = replace(version, claim=replace(version.claim, polarity="negative"))
        elif change == "literal":
            bad = replace(version, claim=replace(version.claim, object_kind="literal"))
        elif change == "invalid":
            bad = None
        monkeypatch.setattr(graph, "iter_claims_as_of", lambda **kwargs: iter([bad]))
        with pytest.raises(ProvenanceMismatchError):
            source.evaluate(_compiled(), limits=ReasoningLimits())


def test_identity_verification_never_initializes_an_unmanaged_handle(graph):
    with graph.read_view(valid_time=50) as view:
        unmanaged = GraphDB(_MetadataStore(), JSONSerializer())
        before = dict(unmanaged.store.metadata)
        forged = GraphReadView(unmanaged, view.provenance)
        with pytest.raises(ProvenanceMismatchError, match="uninitialized"):
            _source(forged)
        assert unmanaged._database_id is None
        assert unmanaged.store.metadata == before


def test_persisted_identity_swap_is_detected_even_when_graph_cache_is_unchanged(graph):
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        graph.store.put_metadata(_DATABASE_ID_METADATA_KEY, b"different")
        with pytest.raises(ProvenanceMismatchError, match="persisted"):
            list(source.iter_evidence())


def test_close_invalidates_pending_iterators_but_not_the_caller_graph(graph, monkeypatch):
    _claim(graph, subject="a")
    _claim(graph, subject="b")
    seen = []
    original = graph.iter_claims_as_of

    def observed(**kwargs):
        for value in original(**kwargs):
            seen.append(value)
            yield value

    monkeypatch.setattr(graph, "iter_claims_as_of", observed)
    with graph.read_view(valid_time=50) as view:
        source = _source(view)
        iterator = source.iter_evidence()
        next(iterator)
        source.close()
        with pytest.raises(ReasoningError, match="closed"):
            next(iterator)
        assert len(seen) == 1
        with pytest.raises(ReasoningError, match="closed"):
            source.__enter__()
    assert _claim(graph, subject="still-open")


def test_stale_indexes_are_not_rebuilt_implicitly(graph):
    _claim(graph, index_mode=IndexMaintenanceMode.DEFER)
    before = copy.deepcopy((graph.store.metadata, graph.store.indexes, graph.store.range_indexes))
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        with pytest.raises(RuntimeError, match="stale indexes"):
            source.evaluate(_compiled(), limits=ReasoningLimits())
    assert before == (graph.store.metadata, graph.store.indexes, graph.store.range_indexes)


def test_result_rejects_context_policy_mapping_or_revision_mixing(graph, tmp_path):
    _claim(graph)
    compiled = _compiled()
    other = _compiled("different-id-same-program")
    assert other.program.digest == compiled.program.digest
    with graph.read_view(valid_time=50) as view, _source(view) as source:
        with pytest.raises(ProvenanceMismatchError, match="revision"):
            source.evaluate(other, limits=ReasoningLimits())
        result = source.evaluate(compiled, limits=ReasoningLimits())
    for changes in (
        dict(compiled=other), dict(selection=ClaimSelection(include_derived=True)),
        dict(mapping=ClaimMapping([("IS_A", TYPE_PREDICATE)])),
        dict(context=replace(result.context, world="other")),
    ):
        with pytest.raises(ProvenanceMismatchError):
            replace(result, **changes)
    with pytest.raises(FrozenInstanceError):
        result.context = None
    other_graph = GraphDB(_MetadataStore(), JSONSerializer())
    other_graph._backend_name = "leveldb"
    other_graph.save_manifest(tmp_path / "different-graph")
    with pytest.raises(ProvenanceMismatchError):
        result.verify_source(other_graph)


def test_empty_view_and_repeat_construction_have_stable_context_identity(graph):
    with graph.read_view(valid_time=50) as first, graph.read_view(valid_time=50) as second:
        with _source(first) as a, _source(second) as b:
            assert a.context == b.context
            assert a.evaluate(_compiled(), limits=ReasoningLimits()).facts == ()
    with graph.read_view(valid_time=51) as other, _source(other) as source:
        assert source.context != a.context


def test_policy_and_mapping_values_are_canonical_copied_and_validated():
    agents = ["b", "a", "a"]
    selection = ClaimSelection(agents=agents, sources=["z", None, "a"])
    agents.clear()
    assert selection.agents == ("a", "b")
    assert selection == ClaimSelection(agents={"a", "b"}, sources={None, "a", "z"})
    assert selection.policy_id == ClaimSelection(agents=["b", "a"], sources=["a", "z", None]).policy_id
    assert ClaimSelection(sources=None).policy_id != ClaimSelection(sources=[None]).policy_id
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ReasoningError):
            ClaimSourceLimits(bad)
    for build in (
        lambda: ClaimSelection(agents="agent"), lambda: ClaimSelection(agents=[None]),
        lambda: ClaimSelection(sources=[""]), lambda: ClaimSelection(include_derived=1),
        lambda: ClaimMapping([("p", "q"), ("p", "r")]), lambda: ClaimMapping([("p",)]),
        lambda: ClaimMapping([("p", "")]),
    ):
        with pytest.raises(ReasoningError):
            build()
    with pytest.raises(ReasoningError):
        PinnedClaimSource(object(), world="w", ontology_revision="o", selection=ClaimSelection(), mapping=ClaimMapping())


def test_adapter_works_on_available_backends(graph_db, tmp_path):
    graph_db.save_manifest(tmp_path / "adapter-managed")
    _claim(graph_db)
    with graph_db.read_view(valid_time=50) as view, _source(view) as source:
        result = source.evaluate(_compiled(), limits=ReasoningLimits(), collect_proofs=True)
        assert Fact("alice", TYPE_PREDICATE, "Person") in result
        result.verify_source(graph_db)


def test_historical_system_time_and_retraction_do_not_change_a_pinned_prefix(graph):
    first = _claim(graph)
    with graph.read_view(valid_time=30) as original, _source(original) as source:
        original_result = source.evaluate(_compiled(), limits=ReasoningLimits())
        graph.retract_claim(first.logical_id, supersedes_version_id=first.version_id, valid=(20, 40))
        assert source.evaluate(_compiled(), limits=ReasoningLimits()) == original_result
        with graph.read_view(valid_time=30, system_time=first.system_time) as historical, _source(historical) as old:
            assert old.context == original_result.context
        with graph.read_view(valid_time=30) as current, _source(current) as now:
            assert now.evaluate(_compiled(), limits=ReasoningLimits()).facts == ()
        with graph.read_view(valid_time=50) as outside, _source(outside) as active:
            assert Fact("alice", TYPE_PREDICATE, "Person") in active.evaluate(_compiled(), limits=ReasoningLimits())


def test_transitive_rules_cannot_join_premises_across_worlds(graph):
    from gestaltdb.ontology.model import Transitive

    compiled = compile_ontology(Ontology("worlds", [Transitive("p")]))
    _claim(graph, "a", "p", "b", world="one")
    _claim(graph, "b", "p", "c", world="two")
    with graph.read_view(valid_time=50) as view:
        contexts = []
        for world in ("one", "two"):
            with _source(view, compiled, world=world) as source:
                result = source.evaluate(compiled, limits=ReasoningLimits())
                assert Fact("a", "p", "c") not in result
                assert len(result.facts) == 1
                contexts.append(result.context)
        assert contexts[0] != contexts[1]


def test_claims_documentation_helper_runs_against_a_real_pinned_view(graph):
    _claim(graph)
    document = Path(__file__).parents[1] / "docs" / "ontology-contract.rst"
    block = document.read_text().split(".. code-block:: python\n")[-1]
    lines = []
    for line in block.splitlines():
        if line and not line.startswith("   "):
            break
        lines.append(line)
    namespace = {}
    exec(compile(textwrap.dedent("\n".join(lines)), str(document), "exec"), namespace)
    with graph.read_view(valid_time=50) as view:
        result = namespace["reason_over_claims"](view, _compiled(), world="default")
        assert Fact("alice", TYPE_PREDICATE, "Person") in result


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_result_provenance_verifies_after_managed_database_reopen(tmp_path, backend, module):
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"optional {module} backend is not installed")
    path = tmp_path / "database"
    graph = GraphDB.create(path, backend=backend, serializer="json")
    try:
        _claim(graph)
        with graph.read_view(valid_time=50) as view, _source(view) as source:
            result = source.evaluate(_compiled(), limits=ReasoningLimits(), collect_proofs=True)
    finally:
        graph.close()
    reopened = GraphDB.open(path)
    try:
        result.verify_source(reopened)
        assert result.explain(Fact("alice", TYPE_PREDICATE, "Person")).nodes
    finally:
        reopened.close()
