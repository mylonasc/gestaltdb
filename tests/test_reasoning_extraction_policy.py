"""Request restrictions and claim selection intersect; neither weakens the other."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.extraction import ExtractionRequest


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    for subject, agent, source in [("a", "registry", "trusted"),
                                    ("c", "other", "untrusted"), ("e", "registry", None)]:
        graph.epistemic.assert_claim(subject=subject, predicate="p", object="b", agent=agent,
                           source=source, polarity="positive", valid=(0, 100))
    try:
        yield graph
    finally:
        graph.close()


@pytest.mark.parametrize("request_args,selection_args,expected", [
    ({"agents": []}, {}, set()),
    ({"agents": ["registry"]}, {}, {"a", "e"}),
    ({"agents": ["other"]}, {"agents": ["registry"]}, set()),
    ({}, {"agents": ["registry"]}, {"a", "e"}),
    ({"sources": ["trusted"]}, {}, {"a"}),
    ({}, {"sources": [None]}, {"e"}),
    ({"sources": [None]}, {}, {"e"}),
    ({"sources": []}, {}, set()),
    ({"sources": ["trusted"]}, {"sources": ["untrusted"]}, set()),
    ({"agents": ["registry"], "sources": ["trusted"]}, {}, {"a"}),
])
def test_effective_policy_intersects_request_and_selection(graph, request_args, selection_args, expected):
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        with PinnedBatchedClaimSource(
            view, request=ExtractionRequest(world="default", **request_args), ontology_revision="rev",
            selection=ClaimSelection(**selection_args), mapping=ClaimMapping(),
        ) as source:
            subjects = {e.fact.subject for batch in source.iter_batches() for e in batch.evidence}
    assert subjects == expected


def test_equivalent_effective_policies_bind_the_same_context(graph):
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        contexts = []
        for request_args, selection in [({"agents": ["registry"]}, ClaimSelection()),
                                         ({}, ClaimSelection(agents=["registry"]))]:
            with PinnedBatchedClaimSource(
                view, request=ExtractionRequest(world="default", **request_args), ontology_revision="rev",
                selection=selection, mapping=ClaimMapping(),
            ) as source:
                contexts.append(source.context)
        assert contexts[0] == contexts[1]
