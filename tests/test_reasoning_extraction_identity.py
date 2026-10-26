"""Logical extraction scopes, not batch sizing, participate in source identity."""

from dataclasses import replace

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.ontology import Ontology, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
from gestaltdb.reasoning.extraction import ExtractionRequest
from gestaltdb.reasoning.model import ReasoningLimits


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.epistemic.assert_claim(subject="a", predicate="knows", object="b", agent="registry",
                       polarity="positive", valid=(0, 100))
    graph.epistemic.assert_claim(subject="c", predicate="likes", object="d", agent="other",
                       polarity="positive", valid=(0, 100))
    try:
        yield graph
    finally:
        graph.close()


def source(view, request, compiled):
    return PinnedBatchedClaimSource(view, request=request, ontology_revision=compiled.revision_id,
                                    selection=ClaimSelection(), mapping=ClaimMapping())


def test_each_logical_restriction_binds_a_distinct_context(graph):
    compiled = compile_ontology(Ontology("o", []))
    restrictions = [{}, {"predicates": []}, {"predicates": ["knows"]}, {"predicates": ["likes"]},
                    {"subject": "a"}, {"subject": "c"}, {"object": "b"}, {"object": "d"}]
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        contexts = []
        for restriction in restrictions:
            with source(view, ExtractionRequest(world="default", **restriction), compiled) as selected:
                contexts.append(selected.context)
        assert len(set(contexts)) == len(restrictions)


def test_physical_batch_settings_and_predicate_order_do_not_change_identity(graph):
    compiled = compile_ontology(Ontology("o", []))
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        contexts = []
        for predicates, batch_rows, max_rows in [(["knows", "likes"], 1, 10),
                                                   (["likes", "knows", "likes"], 100, 1000)]:
            request = ExtractionRequest(world="default", predicates=predicates,
                                        batch_rows=batch_rows, max_rows=max_rows)
            with source(view, request, compiled) as selected:
                contexts.append(selected.context)
        assert contexts[0] == contexts[1]


def test_results_retain_and_validate_the_normalized_extraction_request(graph):
    compiled = compile_ontology(Ontology("o", []))
    request = ExtractionRequest(world="default", predicates=["knows"], agents=["registry"])
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view, source(view, request, compiled) as selected:
        result = selected.evaluate(compiled, limits=ReasoningLimits())
        assert result.extraction_request == request
        assert result.selection.agents == ("registry",)
        with pytest.raises(ProvenanceMismatchError):
            replace(result, extraction_request=replace(request, predicates=["likes"]))
        with pytest.raises(ProvenanceMismatchError):
            replace(result, extraction_request=replace(request, world="other"))


def test_unrestricted_batched_context_remains_compatible_with_unbatched(graph):
    compiled = compile_ontology(Ontology("o", []))
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        with source(view, ExtractionRequest(world="default"), compiled) as batched:
            with PinnedClaimSource(view, world="default", ontology_revision=compiled.revision_id,
                                   selection=ClaimSelection(), mapping=ClaimMapping()) as single:
                assert batched.context == single.context
