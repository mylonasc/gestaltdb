import pytest

from gestaltdb.graphdb import GraphDB, Node, Edge
from gestaltdb.ontology import Ontology, SubClassOf, SubPropertyOf, Transitive, TYPE_PREDICATE, compile_ontology
from gestaltdb.reasoning import Fact, ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.reasoning.adapters.graph import GraphMapping, GraphSourceLimits, PinnedGraphSource


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.put_nodes([Node("a", labels=["Researcher"], properties={"employer": "org"}), Node("b"), Node("c")])
    graph.put_edge(Edge("ab", "a", "b", {"type": "knows"}))
    graph.put_edge(Edge("ab2", "a", "b", {"type": "knows"}))
    graph.put_edge(Edge("bc", "b", "c", {"type": "knows"}))
    graph.assert_claim(subject="x", predicate="knows", object="y", agent="other", polarity="positive", valid=(0, 100))
    yield graph
    graph.close()


def plan():
    return compile_ontology(Ontology("g", [SubClassOf("Researcher", "Person"), SubPropertyOf("knows", "related"), Transitive("related")]))


def test_explicit_mapping_and_expanded_relation_semantics(graph):
    mapping = GraphMapping(labels=[("Researcher", "Researcher")], edge_types=[("knows", "knows")], node_properties=[("employer", "worksFor")])
    with graph.current_read_view(require_verifiable=True) as view:
        with PinnedGraphSource(view, mapping=mapping) as source:
            result = source.evaluate(plan(), limits=ReasoningLimits(), collect_proofs=True)
    assert Fact("a", TYPE_PREDICATE, "Person") in result.result
    assert Fact("a", "worksFor", "org") in result.result
    assert not result.match(subject="x")
    assert result.sample_neighbors("a", predicate="related", direction="out", fanout=10, seed=1) == ("b", "c")
    assert len(result.sample_neighbors("a", predicate="related", direction="out", fanout=1, seed=1)) == 1
    assert result.sample_neighbors("c", predicate="related", direction="in", fanout=10, seed=1) == ("a", "b")
    assert result.sample_neighbors("a", predicate="related", direction="out", fanout=0, seed=1) == ()
    assert len(result.match(predicate="knows")) == 2  # parallel edges collapse
    assert not result.result.explain(Fact("a", "related", "c")).truncated
    assert len(graph.query("MATCH ()-[r:related]->() RETURN r").records) == 0


def test_empty_mapping_and_record_limit(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        with PinnedGraphSource(view, mapping=GraphMapping()) as source:
            assert not source.evaluate(plan(), limits=ReasoningLimits()).result.facts
        with PinnedGraphSource(view, mapping=GraphMapping(), limits=GraphSourceLimits(1)) as source:
            with pytest.raises(ReasoningLimitError):
                source.evaluate(plan(), limits=ReasoningLimits())


def test_pinning_rebinding_and_lifetime(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        source = PinnedGraphSource(view, mapping=GraphMapping(labels=[("Researcher", "Researcher")]))
        graph.put_node(Node("later", labels=["Researcher"]))
        first = source.evaluate(plan(), limits=ReasoningLimits())
        assert not first.match(subject="later")
        source.close()
        with pytest.raises(ReasoningError, match="closed"):
            source.evaluate(plan(), limits=ReasoningLimits())


def test_rebinding_and_closed_backend_snapshot_fail(graph):
    from gestaltdb.readview import ProvenanceMismatchError
    with graph.current_read_view(require_verifiable=True) as view:
        source = PinnedGraphSource(view, mapping=GraphMapping())
        original = view._graph
        view._graph = graph
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            source.evaluate(plan(), limits=ReasoningLimits())
        view._graph = original
    with pytest.raises(RuntimeError):
        source.evaluate(plan(), limits=ReasoningLimits())


def test_temporal_views_are_not_current_graph_sources(graph):
    with graph.read_view(valid_time=50) as view:
        with pytest.raises(ReasoningError, match="CurrentGraphReadView"):
            PinnedGraphSource(view, mapping=GraphMapping())


def test_forged_current_snapshot_provenance_is_rejected(graph):
    from dataclasses import replace
    from gestaltdb.readview import ProvenanceMismatchError
    with graph.current_read_view(require_verifiable=True) as view:
        original = view.provenance
        view.provenance = replace(original, state_sha256="0" * 64)
        with pytest.raises(ProvenanceMismatchError, match="state digest"):
            PinnedGraphSource(view, mapping=GraphMapping())
        view.provenance = replace(original, database_id="00000000-0000-0000-0000-000000000000")
        with pytest.raises(ProvenanceMismatchError, match="database identity"):
            PinnedGraphSource(view, mapping=GraphMapping())


def test_property_literals_are_not_silently_coerced(graph):
    graph.put_node(Node("bad", properties={"employer": 42}))
    with graph.current_read_view(require_verifiable=True) as view:
        with PinnedGraphSource(view, mapping=GraphMapping(node_properties=[("employer", "p")])) as source:
            with pytest.raises(ReasoningError):
                source.evaluate(plan(), limits=ReasoningLimits())


@pytest.mark.parametrize("kwargs", [{"labels": [("A", "B"), ("A", "C")]}, {"edge_types": "foo"}, {"node_properties": [("p", "")]}])
def test_mapping_validation(kwargs):
    with pytest.raises(ReasoningError):
        GraphMapping(**kwargs)


def test_current_graph_guide_example():
    import textwrap
    from pathlib import Path
    pytest.importorskip("lmdb")
    document = Path(__file__).parents[1] / "docs" / "ontology-graph.rst"
    block = document.read_text().split(".. code-block:: python\n")[1]
    lines = []
    for line in block.splitlines():
        if line and not line.startswith("   "):
            break
        lines.append(line)
    exec(compile(textwrap.dedent("\n".join(lines)), str(document), "exec"), {})
