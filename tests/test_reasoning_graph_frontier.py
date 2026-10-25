"""Current-graph demand: projection parity, indexed access and snapshot isolation."""

from dataclasses import replace
import random

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ontology import Ontology, SubClassOf, SubPropertyOf, Transitive, TYPE_PREDICATE, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning import Atom, Fact, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, Rule, Variable
from gestaltdb.reasoning.adapters.graph import GraphMapping, GraphSourceLimits, PinnedGraphSource
from gestaltdb.reasoning.adapters.graph_frontier import answer_current_graph
from gestaltdb.reasoning.engine import DatalogEngine, _unify
from gestaltdb.reasoning.model import ReasoningCancelledError

X, Y, Z = Variable("x"), Variable("y"), Variable("z")
MAPPING = GraphMapping(labels=(("Student", "Student"), ("Learner", "Student")),
                       edge_types=(("knows", "edge"), ("friend", "edge"), ("direct", "reach")),
                       node_properties=(("employer", "worksFor"), ("mentor", "edge")))


def program():
    return Program([
        Rule("base", [Atom(X, "edge", Y)], Atom(X, "reach", Y)),
        Rule("step", [Atom(X, "reach", Y), Atom(Y, "edge", Z)], Atom(X, "reach", Z)),
        Rule("type", [Atom(X, TYPE_PREDICATE, "Student")], Atom(X, TYPE_PREDICATE, "Person")),
    ])


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json",
                           backend_options={"map_size": 128 * 1024**2})
    graph.put_nodes([Node("a", labels=["Student", "Learner"], properties={"employer": "org", "mentor": "d"}),
                     Node("b"), Node("c"), Node("d"), Node("e")])
    for key, s, t, typ in [("ab", "a", "b", "knows"), ("ab2", "a", "b", "knows"),
                           ("bc", "b", "c", "friend"), ("ca", "c", "a", "knows"),
                           ("de", "d", "e", "knows"), ("ad", "a", "d", "direct")]:
        graph.put_edge(Edge(key, s, t, {"type": typ}))
    for i in range(20):
        graph.put_node(Node(f"noise{i}", labels=["Noise"]))
        graph.put_edge(Edge(f"noise-edge{i}", f"noise{i}", "outside", {"type": "knows"}))
    graph.assert_claim(subject="a", predicate="edge", object="temporal-only", agent="other",
                       polarity="positive", valid=(0, 100))
    yield graph
    graph.close()


def run(view, goal, p=None, **kwargs):
    return answer_current_graph(view, program() if p is None else p, goal,
                                ontology_revision="revision", mapping=kwargs.pop("mapping", MAPPING),
                                limits=kwargs.pop("limits", ReasoningLimits()), **kwargs)


def parity(view, goal, p=None, mapping=MAPPING):
    p = program() if p is None else p
    with PinnedGraphSource(view, mapping=mapping) as source:
        evidence = tuple(source._evidence())
    full = DatalogEngine().evaluate(p, evidence, limits=ReasoningLimits())
    expected = tuple(f for f in full.facts if f.predicate == goal.predicate and _unify(goal, f, {}) is not None)
    result = run(view, goal, p, mapping=mapping)
    assert result.answer.facts == expected
    assert set(result.answer.evidence) <= set(evidence)
    return result


def test_forward_frontier_aliases_properties_head_assertions_and_parallel_edges(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        result = parity(view, Atom("a", "reach", Y))
        assert {f.object for f in result.answer.facts} == {"a", "b", "c", "d", "e"}
        assert not any(e.fact.object == "temporal-only" for e in result.answer.evidence)
        assert sum(e.fact == Fact("a", "edge", "b") for e in result.answer.evidence) == 2
        assert result.provenance == view.provenance
        assert result.context.mapping_id == MAPPING.mapping_id
        assert result.records_read < 25 + 26  # full projection node+edge visits
        assert dict(result.accesses)["typed-adjacency-out"] > 0
        assert "edge-type-index" not in dict(result.accesses)


@pytest.mark.parametrize("goal", [Atom(X, "reach", "c"), Atom(X, "reach", X),
                                 Atom("a", "reach", "c"), Atom(X, "reach", Y),
                                 Atom(X, TYPE_PREDICATE, "Person"), Atom("missing", "reach", Y)])
def test_backward_unbound_repeated_and_class_goal_parity(graph, goal):
    with graph.current_read_view(require_verifiable=True) as view:
        result = parity(view, goal)
        if goal == Atom(X, "reach", "c"):
            assert dict(result.accesses)["typed-adjacency-in"] > 0
        if goal == Atom(X, TYPE_PREDICATE, "Person"):
            assert dict(result.accesses)["label-index"] > 0
            assert len(result.answer.evidence) == 2


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("goal", [Atom(X, "worksFor", "org"), Atom(X, "worksFor", Y), Atom("a", "worksFor", Y)])
def test_property_exact_prefix_and_complete_unindexed_strategy(graph, indexed, goal):
    if indexed:
        graph.create_node_property_index("employer")
    with graph.current_read_view(require_verifiable=True) as view:
        result = parity(view, goal)
        strategies = dict(result.accesses)
        if goal.subject == "a":
            assert set(strategies) == {"node-id"}
        elif indexed:
            assert set(strategies) == {"property-exact-index" if goal.object == "org" else "property-prefix-index"}
        else:
            assert set(strategies) == {"property-record-scan"}


def test_disconnected_join_constants_repeated_atoms_and_compiled_ontology(graph):
    p = Program([Rule("disconnected", [Atom(X, TYPE_PREDICATE, "Student"), Atom(Y, "worksFor", Z),
                                      Atom(Y, "worksFor", Z)], Atom(X, "cohort", Z))])
    compiled = compile_ontology(Ontology("ontology", [SubClassOf("Student", "Person"),
                                                       SubPropertyOf("edge", "related"), Transitive("related")]))
    with graph.current_read_view(require_verifiable=True) as view:
        parity(view, Atom("a", "cohort", Y), p)
        result = parity(view, Atom("a", "related", Y), compiled.program)
        assert Fact("a", "related", "c") in result.answer.facts


def test_random_graph_projection_parity(graph):
    rng = random.Random(350)
    for i in range(25):
        key = f"r{i}"
        graph.put_edge(Edge(key, rng.choice("abcde"), rng.choice("abcde"), {"type": rng.choice(["knows", "friend", "direct"])}))
        with graph.current_read_view(require_verifiable=True) as view:
            # Independent canonical-record projection + relational grounding;
            # no adapter mapping helper, index or engine unification is reused.
            edges, reach = set(), set()
            for key in view.store.get_edge_keys_generator():
                edge = view.get_edge(key)
                if edge.properties["type"] in ("knows", "friend"):
                    edges.add((edge.source, edge.target))
                elif edge.properties["type"] == "direct":
                    reach.add((edge.source, edge.target))
            for key in view.store.get_node_keys_generator():
                node = view.get_node(key)
                if "mentor" in node.properties:
                    edges.add((node.get_id, node.properties["mentor"]))
            reach.update(edges)
            while True:
                added = {(a, c) for a, b in reach for u, c in edges if b == u}
                if added <= reach:
                    break
                reach.update(added)
            for goal in (Atom("a", "reach", Y), Atom(X, "reach", "e"), Atom(X, "reach", X)):
                result = parity(view, goal)
                expected = {Fact(a, "reach", b) for a, b in reach
                            if (goal.subject != "a" or a == "a") and (goal.object != "e" or b == "e")
                            and (goal.subject != goal.object or a == b)}
                assert set(result.answer.facts) == expected


def test_live_mutation_isolation_and_snapshot_lifetime(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        graph.put_edge(Edge("new", "b", "new-target", {"type": "knows"}))
        graph.delete_edge(b"bc")
        result = run(view, Atom("a", "reach", Y))
        assert Fact("a", "reach", "c") in result.answer.facts
        assert Fact("a", "reach", "new-target") not in result.answer.facts
    with pytest.raises(RuntimeError):
        run(view, Atom("a", "reach", Y))
    with graph.current_read_view(require_verifiable=True) as latest:
        assert Fact("a", "reach", "new-target") in run(latest, Atom("a", "reach", Y)).answer.facts


def test_rebinding_and_forged_snapshot_are_rejected(graph, monkeypatch):
    with graph.current_read_view(require_verifiable=True) as view:
        provenance = view.provenance
        view.provenance = replace(provenance, state_sha256="0" * 64)
        with pytest.raises(ProvenanceMismatchError, match="digest"):
            run(view, Atom("a", "reach", Y))
        view.provenance = provenance
        original = view.get_edge
        def rebind(key):
            value = original(key)
            view._graph = graph
            return value
        monkeypatch.setattr(view, "get_edge", rebind)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            run(view, Atom("a", "reach", Y))


def test_budgets_cancellation_and_invalid_entity_properties(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        with pytest.raises(ReasoningLimitError, match="max_records"):
            run(view, Atom("a", "reach", Y), source_limits=GraphSourceLimits(1))
        with pytest.raises(ReasoningLimitError, match="max_requests"):
            run(view, Atom("a", "reach", Y), max_requests=1)
        with pytest.raises(ReasoningLimitError, match="max_input_facts"):
            run(view, Atom("a", "reach", Y), limits=ReasoningLimits(max_input_facts=1))
        with pytest.raises(ReasoningCancelledError):
            run(view, Atom("a", "reach", Y), cancel=lambda: True)
        assert run(view, Atom("a", "reach", Y)).answer.facts
    graph.put_node(Node("bad", properties={"employer": 42}))
    with graph.current_read_view(require_verifiable=True) as view:
        with pytest.raises(ReasoningError, match="mapped entity property"):
            run(view, Atom("bad", "worksFor", Y))


def test_mid_read_cancellation_and_exact_record_budget(graph, monkeypatch):
    with graph.current_read_view(require_verifiable=True) as view:
        goal = Atom("a", "reach", Y)
        result = run(view, goal)
        exact = run(view, goal, source_limits=GraphSourceLimits(result.records_read))
        assert exact.answer.facts == result.answer.facts
        reads = []
        original = view.get_edge
        def counted(key):
            reads.append(key)
            return original(key)
        monkeypatch.setattr(view, "get_edge", counted)
        with pytest.raises(ReasoningCancelledError):
            run(view, goal, cancel=lambda: len(reads) >= 2)
        assert len(reads) == 2


def test_stale_indexes_and_unsupported_views_fail_closed(graph, monkeypatch):
    with graph.current_read_view(require_verifiable=True) as view:
        monkeypatch.setattr(view, "stale_indexes", lambda: ("node_property",))
        with pytest.raises(ReasoningError, match="stale"):
            run(view, Atom("a", "reach", Y))
    with graph.read_view(valid_time=50) as temporal:
        with pytest.raises(ReasoningError, match="CurrentGraphReadView"):
            run(temporal, Atom("a", "reach", Y))


def test_unsupported_program_and_missing_provenance(graph):
    from gestaltdb.reasoning.typed import GenericProgram
    with pytest.raises(ReasoningError, match="binary"):
        run(object(), Atom("a", "reach", Y), GenericProgram([]))
    with graph.current_read_view(capture_provenance=False) as view:
        with pytest.raises(ReasoningError, match="verifiable"):
            run(view, Atom("a", "reach", Y))


def test_leveldb_requests_do_not_silently_fall_back(tmp_path):
    pytest.importorskip("plyvel")
    graph = GraphDB.create(tmp_path / "leveldb", backend="leveldb", serializer="json")
    try:
        with pytest.raises(NotImplementedError):
            with graph.current_read_view(require_verifiable=True) as view:
                run(view, Atom("a", "reach", Y))
    finally:
        graph.close()
