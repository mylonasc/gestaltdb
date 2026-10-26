"""#396 richer declarative graph-to-fact mappings (typed v2 projection)."""

from types import SimpleNamespace

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.reasoning import ReasoningError, ReasoningLimitError
from gestaltdb.reasoning.adapters.graph import GraphMapping
from gestaltdb.reasoning.adapters.typed_graph import (
    TYPE_PREDICATE,
    PinnedTypedGraphSource,
    TypedEvidence,
    TypedGraphMapping,
    TypedSourceLimits,
    _typed_edge_evidence,
    _typed_node_evidence,
)
from gestaltdb.reasoning.model import ReasoningLimits
from gestaltdb.reasoning.terms import Literal
from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
from gestaltdb.reasoning.model import Variable


LIMITS = TypedSourceLimits()


def node(node_id, labels=(), properties=None):
    return SimpleNamespace(get_id=node_id, labels=list(labels), properties=dict(properties or {}))


def edge(edge_id, source, target, properties=None):
    return SimpleNamespace(get_id=edge_id, source=source, target=target, properties=dict(properties or {}))


def test_mapping_validation_rejects_malformed_fields():
    with pytest.raises(ReasoningError):
        TypedGraphMapping(labels=["not-a-pair"])
    with pytest.raises(ReasoningError):
        TypedGraphMapping(labels=[("A", "B"), ("A", "C")])
    with pytest.raises(ReasoningError):
        TypedGraphMapping(identity_collision="silent")
    with pytest.raises(ReasoningError):
        TypedSourceLimits(max_elements_per_value=0)
    with pytest.raises(ReasoningError):
        TypedEvidence("not-a-fact", "ref")
    assert TypedGraphMapping().mapping_id != TypedGraphMapping(labels=[("P", "Person")]).mapping_id
    assert TypedGraphMapping().mapping_id != TypedGraphMapping(identity_collision="merge").mapping_id


def test_scalar_attributes_coerce_and_fail_closed():
    record = node("n", properties={"s": "x", "i": 3, "f": 1.5, "b": True,
                                   "none": None, "obj": {"a": 1}, "raw": b"bytes"})
    mapping = TypedGraphMapping(node_attributes=[("s", "name"), ("i", "age"),
                                                 ("f", "score"), ("b", "flag")])
    facts = {item.fact for item in _typed_node_evidence(record, mapping, LIMITS)}
    assert TypedFact("name", ["n", Literal("text", "x")]) in facts
    assert TypedFact("age", ["n", Literal("integer", 3)]) in facts
    assert TypedFact("score", ["n", Literal("float", 1.5)]) in facts
    assert TypedFact("flag", ["n", Literal("boolean", True)]) in facts
    assert isinstance(Literal("integer", 3).value, int)
    for bad in ("none", "obj", "raw"):
        with pytest.raises(ReasoningError):
            list(_typed_node_evidence(record, TypedGraphMapping(node_attributes=[(bad, "p")]), LIMITS))
    # Unmapped fields are ignored, never projected.
    assert list(_typed_node_evidence(node("m", properties={"other": 1}), mapping, LIMITS)) == []


def test_list_expansion_is_bounded_and_explicit():
    record = node("n", properties={"tags": ["a", "b"], "empty": [], "scalar": "x", "big": list(range(200))})
    mapping = TypedGraphMapping(node_lists=[("tags", "tag")])
    items = list(_typed_node_evidence(record, mapping, LIMITS))
    assert [item.fact for item in items] == [TypedFact("tag", ["n", Literal("text", "a")]),
                                             TypedFact("tag", ["n", Literal("text", "b")])]
    assert [item.reference_id for item in items] == [
        'typed:node-list:["n","tags",0]', 'typed:node-list:["n","tags",1]']
    assert list(_typed_node_evidence(record, TypedGraphMapping(node_lists=[("empty", "tag")]), LIMITS)) == []
    with pytest.raises(ReasoningError):
        list(_typed_node_evidence(record, TypedGraphMapping(node_lists=[("scalar", "tag")]), LIMITS))
    with pytest.raises(ReasoningLimitError):
        list(_typed_node_evidence(record, TypedGraphMapping(node_lists=[("big", "tag")]), LIMITS))
    refs = list(_typed_node_evidence(
        node("n", properties={"friends": ["a", "b"]}),
        TypedGraphMapping(node_reference_lists=[("friends", "knows")]), LIMITS))
    assert [item.fact for item in refs] == [TypedFact("knows", ["n", "a"]), TypedFact("knows", ["n", "b"])]
    with pytest.raises(ReasoningError):
        list(_typed_node_evidence(
            node("n", properties={"friends": ["a", 42]}),
            TypedGraphMapping(node_reference_lists=[("friends", "knows")]), LIMITS))


def test_edge_attributes_project_nary_and_unmapped_types_fail_closed():
    record = edge("e1", "a", "b", {"type": "PAYS", "amount": 5, "tags": ["x", "y"],
                                   "parties": ["c"], "junk": {"k": 1}})
    mapping = TypedGraphMapping(edge_types=[("PAYS", "pays")],
                                edge_attributes=[("amount", "amount")],
                                edge_lists=[("tags", "tag")],
                                edge_reference_lists=[("parties", "party")])
    facts = [item.fact for item in _typed_edge_evidence(record, mapping, LIMITS)]
    assert TypedFact("pays", ["a", "b"]) in facts
    assert TypedFact("amount", ["a", "b", Literal("integer", 5)]) in facts
    assert TypedFact("tag", ["a", "b", Literal("text", "x")]) in facts
    assert TypedFact("party", ["a", "b", "c"]) in facts
    refs = {item.reference_id for item in _typed_edge_evidence(record, mapping, LIMITS)}
    assert 'typed:edge:["e1"]' in refs and 'typed:edge-attribute:["e1","amount"]' in refs
    with pytest.raises(ReasoningError):
        list(_typed_edge_evidence(record, TypedGraphMapping(
            edge_types=[("PAYS", "pays")], edge_attributes=[("junk", "j")]), LIMITS))
    # Mapped attributes on an unmapped edge type fail closed instead of leaking.
    with pytest.raises(ReasoningError, match="unmapped"):
        list(_typed_edge_evidence(edge("e2", "a", "b", {"type": "OTHER", "amount": 1}),
                                  TypedGraphMapping(edge_attributes=[("amount", "amount")]), LIMITS))
    # Unmapped edge types without mapped attributes stay silent.
    assert list(_typed_edge_evidence(edge("e3", "a", "b", {"type": "OTHER"}),
                                     TypedGraphMapping(edge_types=[("PAYS", "pays")]), LIMITS)) == []


def test_node_identity_construction_is_deterministic_and_strict():
    record = node("n1", labels=["Person"], properties={"ssn": "S-1", "name": "ann"})
    mapping = TypedGraphMapping(labels=[("Person", "Person")],
                                node_attributes=[("name", "name")],
                                node_identity=[("Person", "ssn")])
    facts = [item.fact for item in _typed_node_evidence(record, mapping, LIMITS)]
    assert facts[0].values[0] == "S-1"
    assert all(fact.values[0] == "S-1" for fact in facts)
    assert [item.reference_id for item in _typed_node_evidence(record, mapping, LIMITS)][0] == \
        'typed:node-label:["n1","Person"]'
    with pytest.raises(ReasoningError):
        list(_typed_node_evidence(node("n2", labels=["Person"], properties={}), mapping, LIMITS))
    with pytest.raises(ReasoningError):
        list(_typed_node_evidence(
            node("n3", labels=["Person"], properties={"ssn": 42}), mapping, LIMITS))
    ambiguous = TypedGraphMapping(node_identity=[("A", "p"), ("B", "q")])
    with pytest.raises(ReasoningError, match="ambiguous"):
        list(_typed_node_evidence(
            node("n4", labels=["A", "B"], properties={"p": "x", "q": "y"}), ambiguous, LIMITS))


def test_v1_mapping_is_untouched():
    first = GraphMapping(labels=[("P", "Person")], edge_types=[("K", "knows")],
                         node_properties=[("nick", "nickname")])
    assert first.mapping_id == GraphMapping(
        node_properties=[("nick", "nickname")], edge_types=[("K", "knows")],
        labels=[("P", "Person")]).mapping_id
    assert first.mapping_id.startswith("sha256:")


def test_projection_is_repeatable():
    record = node("n", labels=["P"], properties={"a": 1, "tags": ["x", "y"]})
    mapping = TypedGraphMapping(labels=[("P", "C")], node_attributes=[("a", "a")],
                                node_lists=[("tags", "t")])
    first = list(_typed_node_evidence(record, mapping, LIMITS))
    second = list(_typed_node_evidence(record, mapping, LIMITS))
    assert first == second


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    graph.put_nodes([Node("a", labels=["Person"], properties={"name": "ann", "age": 30,
                                                              "tags": ["vip", "staff"]} ),
                     Node("b", labels=["Person"], properties={"name": "bob", "age": 40})])
    graph.put_edge(Edge("ab", "a", "b", {"type": "PAYS", "amount": 5}))
    yield graph
    graph.close()


X = Variable("x")


def test_end_to_end_projection_evaluation_and_pinning(graph):
    mapping = TypedGraphMapping(labels=[("Person", "Person")],
                                node_attributes=[("name", "name"), ("age", "age")],
                                node_lists=[("tags", "tag")],
                                edge_types=[("PAYS", "pays")],
                                edge_attributes=[("amount", "amount")])
    program = GenericProgram([GenericRule("adult", [TypedAtom("age", [X, Literal("integer", 30)])],
                                          [], TypedAtom("status", [X, Literal("text", "adult")]))])
    with graph.current_read_view(require_verifiable=True) as view:
        with PinnedTypedGraphSource(view, mapping=mapping) as source:
            evidence = list(source.iter_evidence())
            context = source.context("rev-1")
    facts = [item.fact for item in evidence]
    assert TypedFact("name", ["a", Literal("text", "ann")]) in facts
    assert TypedFact("tag", ["a", Literal("text", "vip")]) in facts
    assert TypedFact("amount", ["a", "b", Literal("integer", 5)]) in facts
    assert all(item.reference_id.startswith("typed:") for item in evidence)
    assert context.mapping_id == mapping.mapping_id
    closure = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
    assert TypedFact("status", ["a", Literal("text", "adult")]) in closure.facts
    # A mapping change yields a new pinning token: stale caches cannot match.
    changed = TypedGraphMapping(labels=[("Person", "Person")],
                                node_attributes=[("name", "name")])
    assert changed.mapping_id != mapping.mapping_id


def test_identity_collision_fails_closed_and_merge_is_explicit(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        db.put_nodes([Node("a", labels=["Person"], properties={"key": "same", "name": "ann"}),
                      Node("b", labels=["Person"], properties={"key": "same", "name": "bob"})])
        mapping = TypedGraphMapping(labels=[("Person", "Person")],
                                    node_attributes=[("name", "name")],
                                    node_identity=[("Person", "key")])
        with db.current_read_view(require_verifiable=True) as view:
            with PinnedTypedGraphSource(view, mapping=mapping) as source:
                with pytest.raises(ReasoningError, match="identity collision"):
                    list(source.iter_evidence())
        merged = TypedGraphMapping(labels=[("Person", "Person")],
                                   node_attributes=[("name", "name")],
                                   node_identity=[("Person", "key")],
                                   identity_collision="merge")
        with db.current_read_view(require_verifiable=True) as view:
            with PinnedTypedGraphSource(view, mapping=merged) as source:
                evidence = list(source.iter_evidence())
        facts = {item.fact for item in evidence}
        assert TypedFact(TYPE_PREDICATE, ["same", "Person"]) in facts
        assert TypedFact("name", ["same", Literal("text", "ann")]) in facts
        assert TypedFact("name", ["same", Literal("text", "bob")]) in facts
        # Merge unifies subjects; projected assertions still name their records.
        refs = [item.reference_id for item in evidence]
        assert len(refs) == len(set(refs))
        assert 'typed:node-label:["a","Person"]' in refs
        assert 'typed:node-label:["b","Person"]' in refs
        assert mapping.mapping_id != merged.mapping_id
    finally:
        db.close()


def test_identity_collision_with_unchanged_record_id_fails_closed(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        db.put_nodes([Node("same"), Node("n2", labels=["Person"], properties={"key": "same"})])
        mapping = TypedGraphMapping(labels=[("Person", "Person")],
                                    node_identity=[("Person", "key")])
        with db.current_read_view(require_verifiable=True) as view:
            with PinnedTypedGraphSource(view, mapping=mapping) as source:
                with pytest.raises(ReasoningError, match="identity collision"):
                    list(source.iter_evidence())
    finally:
        db.close()


def test_source_references_separate_colon_bearing_components(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        db.put_nodes([Node("a:b", properties={"c": 1}), Node("a", properties={"b:c": 2})])
        mapping = TypedGraphMapping(node_attributes=[("c", "p"), ("b:c", "q")])
        with db.current_read_view(require_verifiable=True) as view:
            with PinnedTypedGraphSource(view, mapping=mapping) as source:
                evidence = list(source.iter_evidence())
        assert len(evidence) == 2
        assert {item.reference_id for item in evidence} == {
            'typed:node-attribute:["a:b","c"]', 'typed:node-attribute:["a","b:c"]'}
    finally:
        db.close()


def test_closed_source_is_rejected(graph):
    mapping = TypedGraphMapping(labels=[("Person", "Person")])
    with graph.current_read_view(require_verifiable=True) as view:
        source = PinnedTypedGraphSource(view, mapping=mapping)
        source.close()
        with pytest.raises(ReasoningError):
            list(source.iter_evidence())
        with pytest.raises(ReasoningError):
            source.context("rev-1")
