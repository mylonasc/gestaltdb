"""Pinned logical queries are explicit, read-only and generation-bound."""

from dataclasses import replace
from pathlib import Path
import textwrap

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ontology import Ontology, SubClassOf, SubPropertyOf, Transitive, compile_ontology
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning import ReasoningError, ReasoningLimits, ReasoningLimitError
from gestaltdb.reasoning.adapters.generation import catalog_for_ontology
from gestaltdb.reasoning.adapters.graph import GraphMapping, GraphSourceLimits
from gestaltdb.reasoning.adapters.queries import QueryLimits, QuerySelection, ReasoningQueryView
from gestaltdb.reasoning.model import ReasoningCancelledError


MAPPING = GraphMapping(labels=(("Employee", "Employee"),), edge_types=(("LINK", "edge"),))
SELECTION = QuerySelection(labels=(("Employee", "Employee"), ("Person", "Person")),
                           edge_types=(("LINK", "edge"), ("REACH", "reach")))
ONTOLOGY = compile_ontology(Ontology("query-test", [SubClassOf("Employee", "Person"),
                            SubPropertyOf("edge", "reach"), Transitive("reach")]))


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.put_nodes([Node("a", labels=["Employee"], properties={"secret": "not projected"}),
                     Node("b"), Node("c"), Node("isolated")])
    graph.put_edges_bulk([Edge("ab", "a", "b", {"type": "LINK"}),
                          Edge("ab2", "a", "b", {"type": "LINK"}),
                          Edge("bc", "b", "c", {"type": "LINK"})])
    yield graph
    graph.close()


def prepare(graph, view, **kwargs):
    return ReasoningQueryView(graph, view, ONTOLOGY, catalog_for_ontology(ONTOLOGY), mapping=MAPPING,
                              selection=kwargs.pop("selection", SELECTION),
                              limits=kwargs.pop("limits", ReasoningLimits()), **kwargs)


def query(prepared, text, *, mode="asserted-plus-inferred", **kwargs):
    return prepared.query(text, expected_generation=kwargs.pop("expected_generation", prepared.generation),
                          mode=mode, **kwargs)


def test_modes_inferred_relationships_provenance_and_no_writes(graph):
    ordinary = 'MATCH (a {id: "a"})-[:REACH]->(b) RETURN id(b) AS target'
    assert graph.query(ordinary).records == []
    with graph.current_read_view(require_verifiable=True) as view:
        before = view.provenance
        prepared = prepare(graph, view)
        result = query(prepared, ordinary)
        assert result.result.records == [{"target": "b"}, {"target": "c"}]
        assert result.generation == prepared.generation
        assert result.selection == SELECTION
        assert result.mode == "asserted-plus-inferred"
        assert query(prepared, ordinary, mode="asserted-only").result.records == []
        assert query(prepared, 'MATCH (a)-[:LINK]->(b) RETURN count(*) AS count',
                     mode="asserted-only").result.records == [{"count": 2}]  # not 3 parallel records
        assert query(prepared, 'MATCH (a {id: "a"})-[r]->(b) '
                     'RETURN DISTINCT r.reasoning_origin AS origin ORDER BY origin').result.records == [
                         {"origin": "asserted"}, {"origin": "inferred"}]
    with graph.current_read_view(require_verifiable=True) as after:
        assert before == after.provenance
    assert graph.query(ordinary).records == []


def test_labels_properties_node_membership_and_selection(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        text = 'MATCH (n:Person) RETURN id(n) AS id, n.secret AS secret, labels(n) AS labels'
        assert query(prepared, text, mode="asserted-only").result.records == []
        assert query(prepared, text).result.records == [
            {"id": "a", "secret": None, "labels": ["Employee", "Person"]}]
        assert query(prepared, 'MATCH (n) RETURN id(n) AS id ORDER BY id').result.records == [
            {"id": "a"}, {"id": "b"}, {"id": "c"}]
        selected = prepare(graph, view, selection=QuerySelection(edge_types=(("PATH", "reach"),)))
        assert query(selected, 'MATCH (n)-[:REACH]->(m) RETURN n').result.records == []
        assert len(query(selected, 'MATCH (n)-[:PATH]->(m) RETURN n').result) == 3
        assert selected.generation == prepared.generation
        assert selected.selection.digest != prepared.selection.digest


@pytest.mark.parametrize("text,expected", [
    ('MATCH (a {id: $id})-[:REACH]->(b) WHERE id(b) <> "b" RETURN id(b) AS target', [{"target": "c"}]),
    ('MATCH (b)<-[:REACH]-(a {id: $id}) RETURN id(b) AS target ORDER BY target DESC LIMIT 1', [{"target": "c"}]),
    ('MATCH (a {id: $id})-[:REACH]-(b) RETURN count(*) AS count', [{"count": 2}]),
    ('MATCH p = (a {id: $id})-[:REACH]->(b) RETURN length(p) AS length LIMIT 1', [{"length": 1}]),
    ('MATCH (a)-[r:REACH {reasoning_origin: "asserted"}]->(b) RETURN r', []),
    ('MATCH (a)-[r:REACH]->(b) WHERE r.reasoning_origin = "asserted" RETURN r', []),
    ('MATCH (a)-[r:REACH]->(b) WHERE r.reasoning_origin > "z" RETURN r', []),
])
def test_cypher_filters_directions_and_modifiers(graph, text, expected):
    with graph.current_read_view(require_verifiable=True) as view:
        assert query(prepare(graph, view), text, parameters={"id": "a"}).result.records == expected


@pytest.mark.parametrize("text", [
    'CREATE (n)', 'MATCH (n) DELETE n RETURN n',
    'MATCH (n) SET n.x = 1 RETURN n',
    'MATCH (n) CALL { WITH n CREATE (m) RETURN m } RETURN n',
    'CALL kg.entails($x) YIELD status RETURN status',
    'SHOW INDEXES', 'CREATE CONSTRAINT ON (n:N) ASSERT n.x IS UNIQUE',
    'MATCH (a)-[:REACH*]->(b) RETURN a',
    'MATCH (a)-[:REACH]->(b)-[:REACH]->(c) RETURN c',
    'MATCH (a), (b) RETURN a',
    'MATCH (a) OPTIONAL MATCH (a)-[:REACH]->(b) RETURN b',
    'MATCH (a) RETURN a UNION MATCH (b) RETURN b',
    'MATCH (a) WITH a RETURN a',
    'MATCH (a) FOR VALID_TIME AS OF datetime("2026-01-01T00:00:00Z") RETURN a',
])
def test_unsupported_forms_fail_closed_without_writes(graph, text):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        with pytest.raises(ValueError):
            query(prepared, text)
        prepared._binding.verify_source(graph)


@pytest.mark.parametrize("field", ["program_digest", "catalog_digest", "dictionary_id", "semantics_profile"])
def test_exact_generation_pins(graph, field):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        wrong = replace(prepared.generation, **{field: "different"})
        with pytest.raises(ReasoningError, match="drifted"):
            query(prepared, 'MATCH (n) RETURN n', expected_generation=wrong)
        with pytest.raises(ReasoningError, match="drifted"):
            prepare(graph, view, expected_generation=wrong)


def test_source_movement_and_closed_view_rejected(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        graph.put_node(Node("new"))
        with pytest.raises(ProvenanceMismatchError):
            query(prepared, 'MATCH (n) RETURN n')
    with pytest.raises(RuntimeError):
        query(prepared, 'MATCH (n) RETURN n')


def test_rebound_view_and_changed_database_rejected(graph, tmp_path):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        other = GraphDB.create(tmp_path / "other", backend="lmdb", serializer="json")
        try:
            with other.current_read_view(require_verifiable=True) as other_view:
                other_prepared = prepare(other, other_view)
                with pytest.raises(ReasoningError, match="drifted"):
                    query(other_prepared, 'MATCH (n) RETURN n', expected_generation=prepared.generation)
                original = view._graph
                view._graph = other
                try:
                    with pytest.raises(ProvenanceMismatchError, match="rebound"):
                        query(prepared, 'MATCH (n) RETURN n')
                finally:
                    view._graph = original
        finally:
            other.close()


def test_returned_entity_mutation_does_not_poison_prepared_closure(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        first = query(prepared, 'MATCH (n:Person) RETURN n').result.records[0]["n"]
        first.labels = ("Wrong",)
        first.properties["secret"] = "wrong"
        assert len(query(prepared, 'MATCH (n:Person) RETURN n').result) == 1
        assert query(prepared, 'MATCH (n:Person) RETURN n.secret AS secret').result.records == [{"secret": None}]


@pytest.mark.parametrize("field", ["max_nodes", "max_edges", "max_query_reads", "max_result_rows"])
def test_query_limits_raise_no_partial_result(graph, field):
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        with pytest.raises(ReasoningLimitError, match=field):
            query(prepared, 'MATCH (a)-[r]->(b) RETURN r', limits=QueryLimits(**{field: 1}))
        assert len(query(prepared, 'MATCH (a)-[:REACH]->(b) RETURN b').result) == 3


def test_preparation_limits_and_cancellation(graph):
    with graph.current_read_view(require_verifiable=True) as view:
        with pytest.raises(ReasoningLimitError, match="max_records"):
            prepare(graph, view, source_limits=GraphSourceLimits(1))
        with pytest.raises(ReasoningLimitError, match="max_input_facts"):
            prepare(graph, view, limits=ReasoningLimits(max_input_facts=1))
        with pytest.raises(ReasoningCancelledError):
            prepare(graph, view, cancel=lambda: True)
        prepared = prepare(graph, view)
        with pytest.raises(ReasoningCancelledError):
            query(prepared, 'MATCH (n) RETURN n', cancel=lambda: True)
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls > 5
        with pytest.raises(ReasoningCancelledError):
            query(prepared, 'MATCH (n) RETURN n', cancel=cancel)


def test_selection_and_argument_validation(graph):
    assert SELECTION.digest == replace(SELECTION, edge_types=tuple(reversed(SELECTION.edge_types))).digest
    for kwargs in ({}, {"edge_types": (("R", "x"), ("R", "y"))}):
        with pytest.raises(ReasoningError):
            QuerySelection(**kwargs)
    for value in (False, 0, -1, "10"):
        with pytest.raises(ReasoningError):
            QueryLimits(max_query_reads=value)
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        with pytest.raises(ReasoningError, match="mode"):
            query(prepared, 'MATCH (n) RETURN n', mode="implicit")
        with pytest.raises(ReasoningError, match="expected_generation"):
            query(prepared, 'MATCH (n) RETURN n', expected_generation=None)
        with pytest.raises(TypeError):
            prepared.query('MATCH (n) RETURN n')


def test_live_source_change_during_query_rejected(graph, monkeypatch):
    from gestaltdb.reasoning.adapters import queries
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        original = queries.execute_plan
        def execute(*args):
            records = original(*args)
            graph.put_node(Node("changed-during-query"))
            return records
        monkeypatch.setattr(queries, "execute_plan", execute)
        with pytest.raises(ProvenanceMismatchError):
            query(prepared, 'MATCH (n) RETURN n')


def test_query_reuses_precomputed_closure_without_reexecution(graph, monkeypatch):
    from gestaltdb.reasoning.adapters import queries
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        def forbidden(*args, **kwargs):
            pytest.fail("query reran reasoning")
        monkeypatch.setattr(queries, "open_inferred_view", forbidden)
        assert len(query(prepared, 'MATCH (a)-[:REACH]->(b) RETURN b').result) == 3
        assert len(query(prepared, 'MATCH (a)-[:LINK]->(b) RETURN b', mode="asserted-only").result) == 2


def test_self_loop_undirected_and_synthetic_ids_stable(graph):
    graph.put_edge(Edge("aa", "a", "a", {"type": "LINK"}))
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = prepare(graph, view)
        text = 'MATCH (a {id: "a"})-[r:LINK]-(b {id: "a"}) RETURN id(r) AS id'
        result = query(prepared, text)
        assert len(result.result) == 1
        edge_id = result.result.records[0]["id"]
        assert edge_id.startswith("logical:")
        assert query(prepared, text, mode="asserted-only").result.records == result.result.records


def test_fact_both_asserted_and_derivable_is_asserted(graph):
    graph.put_edge(Edge("ac", "a", "c", {"type": "REACH"}))
    mapping = GraphMapping(edge_types=(("LINK", "edge"), ("REACH", "reach")))
    with graph.current_read_view(require_verifiable=True) as view:
        prepared = ReasoningQueryView(graph, view, ONTOLOGY, catalog_for_ontology(ONTOLOGY),
                                      mapping=mapping, selection=SELECTION, limits=ReasoningLimits())
        text = ('MATCH (a {id: "a"})-[r:REACH]->(b {id: "c"}) '
                'RETURN r.reasoning_origin AS origin')
        assert query(prepared, text).result.records == [{"origin": "asserted"}]


@pytest.mark.parametrize("kind", ["sphinx", "examples"])
def test_documented_workflow(kind):
    pytest.importorskip("lmdb")
    root = Path(__file__).resolve().parents[1]
    if kind == "sphinx":
        text = (root / "docs/reasoning-queries.rst").read_text()
        block = text.split(".. code-block:: python\n\n", 1)[1].split("\nSelection and", 1)[0]
        code = textwrap.dedent(block)
    else:
        text = (root / "EXAMPLES.md").read_text().split("## Explicit Reasoning-Aware Cypher", 1)[1]
        code = text.split("```python\n", 1)[1].split("```", 1)[0]
    exec(compile(code, f"<reasoning-queries-{kind}>", "exec"), {})
