"""Real database checks for the opt-in authenticated generation bridge."""

from dataclasses import replace

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.ontology import Domain, Ontology, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
from gestaltdb.reasoning.adapters.generation import bind_claim_generation, catalog_for_ontology
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.generation import CurrentSourceIdentity
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_authenticated_binding_and_explicit_freshness(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        version = graph.epistemic.assert_claim(subject="a", predicate="p", object="b", agent="test",
                                     polarity="positive", valid=(0, 100))
        compiled = compile_ontology(Ontology("o", [Domain("p", "Person")]))
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            with PinnedClaimSource(view, world="default", ontology_revision=compiled.revision_id,
                                   selection=ClaimSelection(), mapping=ClaimMapping()) as source:
                result = source.evaluate(compiled, limits=ReasoningLimits())
        catalog = catalog_for_ontology(compiled)
        binding = bind_claim_generation(graph, result, catalog)
        binding.verify_source(graph)
        with pytest.raises(ProvenanceMismatchError, match="binding mismatch"):
            replace(binding, identity=replace(binding.identity, source=CurrentSourceIdentity(
                binding.identity.source.database_id, binding.identity.source.commit_horizon,
                binding.identity.source.provenance_token)))
        assert binding.identity.catalog_digest == catalog.digest
        assert binding.identity.program_digest == compiled.program.digest
        with pytest.raises(ReasoningError, match="catalog content"):
            bind_claim_generation(graph, result, RuleCatalog(()))
        with pytest.raises(ProvenanceMismatchError, match="binding mismatch"):
            replace(binding, identity=replace(binding.identity, context=replace(result.context, world="other")))
        graph.epistemic.retract_claim(version.logical_id, valid=(0, 100))
        with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
            binding.verify_source(graph)
        binding.verify_source(graph, freshness="historical")
        with pytest.raises(ReasoningError, match="freshness"):
            binding.verify_source(graph, freshness="guess")
    finally:
        graph.close()
