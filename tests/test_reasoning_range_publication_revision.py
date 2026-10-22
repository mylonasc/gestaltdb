"""Range stages bind actual revisions and retain strict, retry-safe target CAS."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.publication import GenerationStaleError, publish_staged_range, stage_range
from gestaltdb.reasoning.model import ReasoningError


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_revision_identity_provenance_and_stale_retry(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        def stage(revision):
            return stage_range(graph, [("a", "p", "b")], valid_start_us=10, valid_end_us=20,
                               evidence_id="evidence", ontology_revision=revision, source_world="base",
                               target_world="derived", publication_key="range", selection=ClaimSelection(),
                               mapping=ClaimMapping())

        with pytest.raises(ReasoningError, match="ontology_revision"):
            stage("")
        first = stage("revision-1")
        assert publish_staged_range(graph, first).asserted == 1
        assert publish_staged_range(graph, first).unchanged == 1
        second = stage("revision-2")
        assert second.generation != first.generation
        assert publish_staged_range(graph, second).corrected == 1
        with pytest.raises(GenerationStaleError):
            publish_staged_range(graph, first)
        with graph.read_view(valid_time=10) as view:
            claims = list(view.iter_claims_as_of(world="derived"))
        assert len(claims) == 1
        assert claims[0].claim.provenance["ontology_revision"] == "revision-2"
    finally:
        graph.close()
