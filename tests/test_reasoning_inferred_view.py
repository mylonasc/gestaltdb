"""#395 opt-in reasoning-aware graph queries over an admitted generation."""

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning import ReasoningError, ReasoningLimits
from gestaltdb.reasoning.adapters.generation import catalog_for_ontology
from gestaltdb.reasoning.adapters.graph import GraphMapping
from gestaltdb.reasoning.adapters.inferred_view import (
    InferredMatch,
    PathPattern,
    open_inferred_view,
)


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.put_nodes([Node("a", labels=["Person"]), Node("b", labels=["Person"]), Node("c", labels=["Org"])])
    graph.put_edge(Edge("ab", "a", "b", {"type": "KNOWS"}))
    yield graph
    graph.close()


def open_view(graph, **kwargs):
    mapping = kwargs.pop("mapping", GraphMapping(labels=[("Person", "Person"), ("Org", "Org")],
                                                 edge_types=[("KNOWS", "knows")]))
    plan = compile_ontology(Ontology("test", [SubPropertyOf("knows", "related")]))
    with graph.current_read_view(require_verifiable=True) as view:
        return open_inferred_view(view, plan, catalog_for_ontology(plan), mapping=mapping,
                                  limits=ReasoningLimits(), **kwargs)


def test_modes_select_asserted_vs_inferred(graph):
    view = open_view(graph)
    inferred = view.match(predicate="related")
    assert [(fact.subject, fact.object) for fact in inferred] == [("a", "b")]
    asserted_only = view.with_mode("asserted-only")
    assert asserted_only.match(predicate="related") == ()
    assert asserted_only.match(predicate="knows") != ()
    assert view.match(predicate="related") != ()
    with pytest.raises(ReasoningError):
        view.with_mode("everything")


def test_path_patterns_match_inferred_edges_with_types(graph):
    view = open_view(graph)
    assert view.query_path(PathPattern(edge="related")) == (InferredMatch("a", "related", "b"),)
    assert view.query_path(PathPattern(edge="related", source_type="Person",
                                        target_type="Person")) == (InferredMatch("a", "related", "b"),)
    assert view.query_path(PathPattern(edge="related", target_type="Org")) == ()
    assert view.query_path(PathPattern(edge="related", source="b")) == ()
    assert view.query_path(PathPattern(edge="knows", direction="in", target="a")) == (
        InferredMatch("a", "knows", "b"),)
    with pytest.raises(ReasoningError):
        view.query_path("not-a-pattern")
    with pytest.raises(ReasoningError):
        PathPattern(edge="")
    with pytest.raises(ReasoningError):
        PathPattern(edge="related", direction="sideways")


def test_answers_carry_generation_and_reject_drift(graph):
    view = open_view(graph)
    view.require_generation(view.generation)
    other = open_view(graph, mapping=GraphMapping(edge_types=[("KNOWS", "knows")]))
    with pytest.raises(ReasoningError, match="drifted"):
        view.require_generation(other.generation)


def test_assert_current_rejects_snapshot_movement(graph, tmp_path):
    mapping = GraphMapping(labels=[("Person", "Person"), ("Org", "Org")],
                           edge_types=[("KNOWS", "knows")])
    plan = compile_ontology(Ontology("test", [SubPropertyOf("knows", "related")]))
    with graph.current_read_view(require_verifiable=True) as live:
        view = open_inferred_view(live, plan, catalog_for_ontology(plan), mapping=mapping,
                                  limits=ReasoningLimits())
        view.assert_current(live)
    second = GraphDB.create(tmp_path / "other", backend="lmdb", serializer="json")
    try:
        second.put_nodes([Node("a", labels=["Person"])])
        with second.current_read_view(require_verifiable=True) as foreign:
            with pytest.raises(ProvenanceMismatchError, match="identity mismatch"):
                view.assert_current(foreign)
    finally:
        second.close()


def test_cypher_stays_asserted_only_and_nothing_mutates(graph):
    view = open_view(graph)
    before = (graph.count_nodes_by_label("Person"), graph.count_edges_by_type("KNOWS"))
    assert view.query_path(PathPattern(edge="related")) != ()
    rows = list(graph.query("MATCH (a)-[r:related]->(b) RETURN a.id"))
    assert rows == []
    assert (graph.count_nodes_by_label("Person"), graph.count_edges_by_type("KNOWS")) == before
    assert view.match() == tuple(sorted(set(view.match())))


def test_queries_are_deterministic(graph):
    view = open_view(graph)
    pattern = PathPattern(edge="related")
    assert view.query_path(pattern) == view.query_path(pattern) == (InferredMatch("a", "related", "b"),)
