"""Latest publication rejects source movement; historical publication is explicit."""

from dataclasses import replace

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.ontology import Domain, Ontology, compile_ontology
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource
from gestaltdb.reasoning.adapters.generation import bind_claim_generation, catalog_for_ontology
from gestaltdb.reasoning.adapters.publication import GenerationStaleError, publish_staged, stage_point
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimits


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def staged_graph(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        version = graph.assert_claim(subject="a", predicate="worksFor", object="org", agent="registry",
                                     polarity="positive", valid=(0, 100))
        compiled = compile_ontology(Ontology("o", [Domain("worksFor", "Person")]))
        staged = stage_point(graph, compiled, valid_time=50, source_world="default", target_world="derived",
                             publication_key="test", selection=ClaimSelection(), mapping=ClaimMapping(),
                             limits=ReasoningLimits())
        yield graph, version, staged
    finally:
        graph.close()


def test_latest_rejects_source_retraction_without_writes(staged_graph):
    graph, version, staged = staged_graph
    graph.retract_claim(version.logical_id, valid=(0, 100))
    with graph.read_view(valid_time=50) as view:
        before = view.provenance.commit_horizon
    with pytest.raises(GenerationStaleError, match="source"):
        publish_staged(graph, staged)
    with graph.read_view(valid_time=50) as view:
        assert view.provenance.commit_horizon == before
        assert list(view.iter_claims_as_of(world="derived")) == []


def test_latest_unchanged_source_publishes_with_provenance(staged_graph):
    graph, _, staged = staged_graph
    published = publish_staged(graph, staged)
    assert published.asserted == 2
    identity = staged.generation_binding.identity
    with graph.read_view(valid_time=50) as view:
        claims = list(view.iter_claims_as_of(world="derived"))
    assert all(v.claim.provenance["reasoning_generation"] == identity.digest for v in claims)
    assert all(v.claim.provenance["program_digest"] == identity.program_digest for v in claims)
    assert all(v.claim.provenance["catalog_digest"] == identity.catalog_digest for v in claims)
    commit = graph.get_temporal_commit(published.commit_id)
    assert commit.metadata["reasoning_generation"] == identity.digest
    with graph.read_view(valid_time=50) as view:
        claims = list(view.iter_claims_as_of(world="derived"))
    assert all(v.claim.provenance["publication_freshness"] == "latest" for v in claims)
    assert all(v.claim.provenance["source_commit_horizon"] == staged.source_provenance.commit_horizon
               for v in claims)


def test_historical_mode_is_explicit_and_records_old_horizon(staged_graph):
    graph, version, staged = staged_graph
    graph.retract_claim(version.logical_id, valid=(0, 100))
    assert publish_staged(graph, staged, freshness="historical").asserted == 2
    with graph.read_view(valid_time=50) as view:
        claims = list(view.iter_claims_as_of(world="derived"))
    assert all(v.claim.provenance["publication_freshness"] == "historical" for v in claims)
    assert all(v.claim.provenance["source_commit_horizon"] == staged.source_provenance.commit_horizon
               for v in claims)


def test_unknown_mode_or_missing_source_provenance_fails_closed(staged_graph):
    graph, _, staged = staged_graph
    with pytest.raises(ReasoningError, match="freshness"):
        publish_staged(graph, staged, freshness="guess")
    with pytest.raises(ReasoningError, match="source provenance"):
        publish_staged(graph, replace(staged, source_provenance=None), freshness="historical")


def test_conservative_latest_guard_also_rejects_unrelated_commits(staged_graph):
    graph, _, staged = staged_graph
    graph.assert_claim(subject="unrelated", predicate="p", object="q", agent="other",
                       polarity="positive", valid=(0, 100))
    with pytest.raises(GenerationStaleError, match="source"):
        publish_staged(graph, staged)
    with pytest.raises(ReasoningError, match="database/time"):
        replace(staged, valid_point_us=51)


def test_stage_carries_shared_generation_contract(staged_graph):
    graph, _, staged = staged_graph
    binding = staged.generation_binding
    compiled = compile_ontology(Ontology("o", [Domain("worksFor", "Person")]))
    assert binding.identity.program_digest == compiled.program.digest
    assert binding.identity.catalog_digest == catalog_for_ontology(compiled).digest
    assert binding.identity.context.context_id == staged.input_context_id
    assert binding.identity.source.database_id == staged.database_id
    assert binding.provenance == staged.source_provenance
    binding.verify_source(graph)
    with graph.read_view(valid_time=50) as view:
        with PinnedClaimSource(view, world="default", ontology_revision=compiled.revision_id,
                               selection=ClaimSelection(), mapping=ClaimMapping()) as source:
            result = source.evaluate(compiled, limits=ReasoningLimits())
    independently_bound = bind_claim_generation(graph, result, catalog_for_ontology(compiled))
    binding.identity.require_compatible(independently_bound.identity)
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, source_world="another-world")
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, policy_id="another-policy")
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, mapping_id="another-mapping")
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, input_context_id="another-context")
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, ontology_revision="another-revision")
    with pytest.raises(ReasoningError, match="ClaimGenerationBinding"):
        replace(staged, generation_binding="not-a-binding")
    assert publish_staged(graph, staged).asserted == 2


@pytest.mark.parametrize("field,value", [
    ("semantics_profile", "unsupported-profile"),
    ("dictionary_id", "another-dictionary"),
])
def test_stage_rejects_incompatible_binding_profile(staged_graph, field, value):
    _, _, staged = staged_graph
    binding = staged.generation_binding
    incompatible = replace(binding, identity=replace(binding.identity, **{field: value}))
    with pytest.raises(ReasoningError, match="generation binding mismatch"):
        replace(staged, generation_binding=incompatible)


def test_legacy_stage_without_shared_binding_retains_freshness_guard(staged_graph):
    graph, version, staged = staged_graph
    legacy = replace(staged, generation_binding=None)
    graph.retract_claim(version.logical_id, valid=(0, 100))
    with pytest.raises(GenerationStaleError, match="source"):
        publish_staged(graph, legacy)
    assert publish_staged(graph, legacy, freshness="historical").asserted == 2
