import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
from gestaltdb.reasoning import ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.publication import reconcile_point


@pytest.fixture(params=["lmdb", "leveldb"])
def graph(tmp_path, request):
    pytest.importorskip("lmdb" if request.param == "lmdb" else "plyvel")
    graph = GraphDB.create(tmp_path / "db", backend=request.param, serializer="json")
    yield graph
    graph.close()


def plan(axioms=None):
    return compile_ontology(Ontology("test", axioms if axioms is not None else [SubPropertyOf("p", "q"), SubPropertyOf("q", "p")]))


def publish(graph, compiled=None, **kw):
    params = dict(valid_time=50, source_world="default", target_world="ontology-output", publication_key="test",
                  selection=ClaimSelection(), mapping=ClaimMapping(), limits=ReasoningLimits())
    params.update(kw)
    return reconcile_point(graph, compiled or plan(), **params)


def assertion(graph, agent="one"):
    return graph.epistemic.assert_claim(subject="a", predicate="p", object="b", agent=agent, polarity="positive", valid=(0, 100))


def outputs(graph, time=50):
    return list(graph.epistemic.iter_claims_as_of(world="ontology-output", valid_time=time))


def test_point_scope_idempotence_and_no_base_mutation(graph):
    assertion(graph)
    first = publish(graph)
    assert first.asserted == 2 and first.commit_id is not None
    second = publish(graph)
    assert second.generation_id == first.generation_id
    assert second.unchanged == 2 and second.commit_id is None
    assert len(outputs(graph)) == 2
    assert not outputs(graph, 49) and not outputs(graph, 51)
    assert len(list(graph.epistemic.iter_claims_as_of(world="default", valid_time=50))) == 1
    assert len({v.commit_id for v in outputs(graph)}) == 1
    third = publish(graph, valid_time=51)
    assert third.asserted == 2 and len(outputs(graph, 51)) == 2
    assert {v.logical_id for v in outputs(graph)}.isdisjoint(v.logical_id for v in outputs(graph, 51))


def test_loss_of_independent_support_and_cycles(graph):
    one, two = assertion(graph), assertion(graph, "two")
    publish(graph)
    graph.epistemic.retract_claim(one.logical_id, supersedes_version_id=one.version_id)
    update = publish(graph)
    assert update.corrected == 2 and len(outputs(graph)) == 2
    graph.epistemic.retract_claim(two.logical_id, supersedes_version_id=two.version_id)
    removed = publish(graph)
    assert removed.retracted == 2 and not outputs(graph)
    assert publish(graph).commit_id is None


def test_revision_changes_and_unrelated_target_claims(graph):
    assertion(graph)
    publish(graph)
    graph.epistemic.assert_claim(subject="unrelated", predicate="x", object="z", agent="user", world="ontology-output", polarity="positive", valid=(0, 100))
    change = publish(graph, plan([]))
    assert change.retracted == 1 and change.corrected == 1
    assert {v.claim.predicate for v in outputs(graph)} == {"p", "x"}


def test_failures_before_commit_and_after_visibility_are_retry_safe(graph, monkeypatch):
    assertion(graph)
    commit = graph.temporal.commit_versions
    def before(*args, **kwargs):
        raise RuntimeError("interrupted before publication")
    monkeypatch.setattr(graph.temporal, "commit_versions", before)
    with pytest.raises(RuntimeError):
        publish(graph)
    assert not outputs(graph)
    def after(*args, **kwargs):
        commit(*args, **kwargs)
        raise RuntimeError("interrupted after publication")
    monkeypatch.setattr(graph.temporal, "commit_versions", after)
    with pytest.raises(RuntimeError):
        publish(graph)
    monkeypatch.setattr(graph.temporal, "commit_versions", commit)
    assert publish(graph).commit_id is None
    assert len(outputs(graph)) == 2


def test_inference_and_target_limits_publish_nothing(graph):
    assertion(graph)
    with pytest.raises(ReasoningLimitError):
        publish(graph, limits=ReasoningLimits(max_facts=1))
    assert not outputs(graph)
    publish(graph)
    with pytest.raises(ReasoningLimitError):
        publish(graph, max_target_claims=1)
    assert len(outputs(graph)) == 2


@pytest.mark.parametrize("kwargs", [{"target_world": "default"}, {"selection": ClaimSelection(include_derived=True)}])
def test_no_implicit_mixing_or_derived_self_support(graph, kwargs):
    with pytest.raises(ReasoningError):
        publish(graph, **kwargs)


def test_publication_guide_example():
    import textwrap
    from pathlib import Path
    pytest.importorskip("lmdb")
    document = Path(__file__).parents[1] / "docs" / "ontology-publication.rst"
    block = document.read_text().split(".. code-block:: python\n")[1]
    lines = []
    for line in block.splitlines():
        if line and not line.startswith("   "):
            break
        lines.append(line)
    exec(compile(textwrap.dedent("\n".join(lines)), str(document), "exec"), {})


def test_writers_cannot_advance_inputs_between_evaluation_and_commit(graph, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from gestaltdb.reasoning import DatalogEngine
    assertion(graph)
    evaluating, release, attempted, finished = Event(), Event(), Event(), Event()
    evaluate = DatalogEngine.evaluate
    def paused(*args, **kwargs):
        evaluating.set()
        assert release.wait(5)
        return evaluate(*args, **kwargs)
    def write():
        attempted.set()
        assertion(graph, "concurrent")
        finished.set()
    monkeypatch.setattr(DatalogEngine, "evaluate", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(publish, graph)
        try:
            assert evaluating.wait(5)
            writer = pool.submit(write)
            assert attempted.wait(5)
            assert not finished.wait(0.05)
        finally:
            release.set()
        first = publication.result(timeout=5)
        writer.result(timeout=5)
    monkeypatch.setattr(DatalogEngine, "evaluate", evaluate)
    second = publish(graph)
    assert first.context != second.context
    assert second.corrected == 2  # fresh base support, not stale receipt reuse


def test_interrupted_nontransactional_marker_never_exposes_partial_closure(graph, monkeypatch):
    from gestaltdb.graphdb import _TEMPORAL_VISIBLE_PREFIX
    from gestaltdb.versioning import TemporalCorruptionError
    if graph.store.supports_transactions:
        pytest.skip("nontransactional publication fault injection")
    assertion(graph)
    put = graph.store.put_metadata
    def interrupted(key, value):
        if key.startswith(_TEMPORAL_VISIBLE_PREFIX):
            raise RuntimeError("interrupted before commit marker")
        return put(key, value)
    monkeypatch.setattr(graph.store, "put_metadata", interrupted)
    with pytest.raises(RuntimeError, match="marker"):
        publish(graph)
    monkeypatch.setattr(graph.store, "put_metadata", put)
    # Nontransactional derived index writes can precede the marker. Reads fail
    # closed until caller-managed recovery; no partial output is exposed.
    with pytest.raises(TemporalCorruptionError):
        outputs(graph)
    with pytest.raises(ReasoningError, match="orphan recovery"):
        publish(graph)
    report = graph.temporal.list_temporal_orphans()
    graph.temporal.reclaim_temporal_orphans(through_commit=report.through_commit)
    graph.temporal.rebuild_temporal_indexes()
    assert not outputs(graph)
    retried = publish(graph)
    assert retried.asserted == 2
    assert len(outputs(graph)) == 2
    assert publish(graph).commit_id is None
