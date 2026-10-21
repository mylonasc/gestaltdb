"""RE-14 staged generations, compare-and-publish and explicit recovery consent."""

import pytest

from gestaltdb.reasoning.adapters.publication import (
    GenerationStaleError,
    StagedPointPublication,
    _stage_fingerprint,
    audit_publication_orphans,
    reclaim_publication_orphans,
)
from gestaltdb.reasoning.model import ReasoningError


def _staged(**overrides):
    base = dict(namespace="ontology-publication:abc", publication_key="k",
                source_world="base", target_world="derived",
                valid_point_us=1000, generation="sha256:x",
                input_context_id="ctx", ontology_revision="rev",
                policy_id="pol", mapping_id="map", database_id="db",
                desired=[("a", "p", "b")], base_fingerprint=_stage_fingerprint([]))
    base.update(overrides)
    return StagedPointPublication(**base)


def test_staged_generation_validates_and_canonicalizes():
    first = _staged(desired=[("b", "p", "a"), ("a", "p", "b"), ("a", "p", "b")])
    assert first.desired == (("a", "p", "b"), ("b", "p", "a"))
    assert first == _staged(desired=[("a", "p", "b"), ("b", "p", "a")])
    with pytest.raises(ReasoningError):
        _staged(namespace="")
    with pytest.raises(ReasoningError):
        _staged(valid_point_us=-1)
    with pytest.raises(ReasoningError):
        _staged(desired=[("only-two", "p")])
    with pytest.raises(ReasoningError):
        _staged(desired="not-a-list")
    assert _stage_fingerprint([]) != _stage_fingerprint([("id", "gen")])
    assert _stage_fingerprint([("b", "g"), ("a", "g")]) == _stage_fingerprint([("a", "g"), ("b", "g")])
    assert issubclass(GenerationStaleError, ReasoningError)


def test_recovery_requires_explicit_consent_and_graph():
    with pytest.raises(ReasoningError, match="GraphDB"):
        audit_publication_orphans(object())
    with pytest.raises(ReasoningError, match="consent"):
        reclaim_publication_orphans(object(), through_commit=0, consent=False)


def test_consent_gate_rejects_without_touching_storage():
    class ExplodingGraph:
        def reclaim_temporal_orphans(self, *, through_commit):
            raise AssertionError("must not touch storage without consent")

    with pytest.raises(ReasoningError, match="consent"):
        reclaim_publication_orphans(ExplodingGraph(), through_commit=1, consent=False)
    with pytest.raises(ReasoningError, match="consent"):
        reclaim_publication_orphans(ExplodingGraph(), through_commit=1)
    with pytest.raises(ReasoningError, match="through_commit"):
        reclaim_publication_orphans(object(), through_commit=-1, consent=True)
    with pytest.raises(ReasoningError, match="GraphDB"):
        reclaim_publication_orphans(ExplodingGraph(), through_commit=1, consent=True)


def test_publish_staged_validates_inputs_without_storage():
    from gestaltdb.reasoning.adapters.publication import publish_staged

    with pytest.raises(ReasoningError, match="StagedPointPublication"):
        publish_staged(object(), "not-staged")
    with pytest.raises(ReasoningError, match="GraphDB"):
        publish_staged(object(), _staged())
    with pytest.raises(ReasoningError, match="max_target_claims"):
        publish_staged(object(), _staged(), max_target_claims=0)


def test_adapters_stay_database_coupled_by_design():
    # Publication adapters intentionally import GraphDB/storage; only the
    # stdlib reasoning core carries a no-site-packages guarantee.
    import gestaltdb.reasoning.adapters.publication as publication

    assert hasattr(publication, "GraphDB")
