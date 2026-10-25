"""Pinned frontier claims: aliases, isolation, historical horizon and budgets."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, ClaimSourceLimits
from gestaltdb.reasoning.adapters.frontier import answer_pinned_claims
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.extraction import ExtractionRequest
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits, Rule, Variable

X, Y, Z = Variable("x"), Variable("y"), Variable("z")


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def graph(request, tmp_path):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    yield graph
    graph.close()


def program():
    return Program([
        Rule("base", [Atom(X, "edge", Y)], Atom(X, "reach", Y)),
        Rule("step", [Atom(X, "reach", Y), Atom(Y, "edge", Z)], Atom(X, "reach", Z)),
    ])


def put(graph, subject, predicate, obj, **kwargs):
    return graph.epistemic.assert_claim(subject=subject, predicate=predicate, object=obj, agent="chosen",
                              polarity="positive", valid=(0, 100), world="base", **kwargs)


def run(view, goal=Atom("a", "reach", Y), **kwargs):
    return answer_pinned_claims(view, program(), goal, world="base", ontology_revision="rev",
                                selection=ClaimSelection(agents=["chosen"]),
                                mapping=ClaimMapping([("storedEdge", "edge")]),
                                limits=kwargs.pop("limits", ReasoningLimits()), **kwargs)


def test_selective_alias_recursive_frontier_and_historical_pin(graph):
    for a, p, b in [("a", "storedEdge", "b"), ("b", "storedEdge", "c"),
                    ("a", "reach", "d"), ("d", "storedEdge", "e"),
                    ("a", "storedEdge", "f"), ("f", "storedEdge", "c"), ("c", "storedEdge", "a")]:
        put(graph, a, p, b)
    for i in range(20):
        put(graph, f"noise-{i}", "storedEdge", "outside")
    put(graph, "a", "storedEdge", "b", source="independent")
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        put(graph, "c", "storedEdge", "future")
        result = run(view)
        assert {fact.object for fact in result.answer.facts} == {"a", "b", "c", "d", "e", "f"}
        assert result.provenance == view.provenance
        assert result.context.world == "base"
        assert len({e.reference_id for e in result.answer.evidence}) == len(result.answer.evidence)
        assert sum(e.fact == Fact("a", "edge", "b") for e in result.answer.evidence) == 2
        with PinnedBatchedClaimSource(view, request=ExtractionRequest("base", predicates=("storedEdge", "edge", "reach")),
                                      ontology_revision="rev", selection=ClaimSelection(agents=["chosen"]),
                                      mapping=ClaimMapping([("storedEdge", "edge")])) as source:
            evidence = [e for batch in source.iter_batches() for e in batch.evidence]
            baseline_scanned = source.profile.scanned
        assert set(result.answer.evidence) <= set(evidence)
        full = DatalogEngine().evaluate(program(), evidence, limits=ReasoningLimits())
        assert result.answer.facts == full.match(subject="a", predicate="reach")
        assert result.scanned_claims < baseline_scanned
        assert not any(e.fact.object == "future" for e in result.answer.evidence)
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as latest:
            assert Fact("a", "reach", "future") in run(latest).answer.facts


def test_bounds_cancellation_and_exact_object_goal(graph):
    put(graph, "a", "storedEdge", "b")
    put(graph, "b", "storedEdge", "c")
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        assert run(view, Atom("a", "reach", "c")).answer.facts == (Fact("a", "reach", "c"),)
        with pytest.raises(ReasoningLimitError, match="max_scanned_claims"):
            run(view, source_limits=ClaimSourceLimits(1))
        with pytest.raises(ReasoningLimitError, match="max_requests"):
            run(view, max_requests=1)
        with pytest.raises(ReasoningCancelledError):
            run(view, cancel=lambda: True)
        assert run(view).answer.facts


def test_policy_world_and_valid_time_isolation(graph):
    put(graph, "a", "storedEdge", "b")
    graph.epistemic.assert_claim(subject="b", predicate="storedEdge", object="wrong-agent", agent="other",
                       polarity="positive", world="base", valid=(0, 100))
    graph.epistemic.assert_claim(subject="b", predicate="storedEdge", object="wrong-world", agent="chosen",
                       polarity="positive", world="other", valid=(0, 100))
    graph.epistemic.assert_claim(subject="b", predicate="storedEdge", object="wrong-time", agent="chosen",
                       polarity="positive", world="base", valid=(100, 200))
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        assert run(view).answer.facts == (Fact("a", "reach", "b"),)


def test_unsupported_profile_fails_before_reads(graph, monkeypatch):
    from gestaltdb.reasoning.typed import GenericProgram
    def unread(*args, **kwargs):
        raise AssertionError("unsupported fragment reached storage")
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
        monkeypatch.setattr(view, "iter_claims_as_of", unread)
        with pytest.raises(ReasoningError, match="binary"):
            answer_pinned_claims(view, GenericProgram([]), Atom("a", "p", Y), world="base",
                                 ontology_revision="rev", selection=ClaimSelection(), mapping=ClaimMapping(),
                                 limits=ReasoningLimits())


def test_frontier_documented_example():
    from pathlib import Path
    import textwrap
    pytest.importorskip("lmdb")
    text = (Path(__file__).parents[1] / "docs/reasoning-frontier.rst").read_text()
    block = text.split(".. code-block:: python\n\n", 1)[1].split("\nSemantics", 1)[0]
    exec(compile(textwrap.dedent(block), "reasoning-frontier.rst", "exec"), {})
