"""RE-08 M1 Horn-safe schema extensions with exact origins and grounding parity."""

import pytest

from gestaltdb.ontology import (
    IntersectionSubClassOf,
    Ontology,
    OntologyError,
    PropertyChain,
    Reflexive,
    SubClassOf,
    TYPE_PREDICATE,
    UnionSubClassOf,
    compile_ontology,
)
from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
from tests.test_reasoning_engine import _reference


def evaluate(compiled, facts):
    return DatalogEngine().evaluate(compiled.program, facts, limits=ReasoningLimits(), collect_proofs=True)


def test_chain_intersection_union_and_reflexive_closure():
    plan = compile_ontology(Ontology("o", [
        PropertyChain("hasParent", "hasBrother", "hasUncle"),
        IntersectionSubClassOf(["Researcher", "Teacher"], "Professor"),
        UnionSubClassOf(["Cat", "Dog"], "Pet"),
        Reflexive("knows", "Person"),
    ]))
    assert {origin.axiom_id for origin in plan.origins} == {axiom.axiom_id for axiom in plan.ontology.axioms}
    assert len({origin.rule_id for origin in plan.origins}) == len(plan.program.rules) == 5
    facts = [Fact("alice", "hasParent", "bob"), Fact("bob", "hasBrother", "carol"),
             Fact("dave", TYPE_PREDICATE, "Researcher"), Fact("dave", TYPE_PREDICATE, "Teacher"),
             Fact("erin", TYPE_PREDICATE, "Researcher"),
             Fact("tom", TYPE_PREDICATE, "Cat"), Fact("jerry", TYPE_PREDICATE, "Dog"),
             Fact("mallory", TYPE_PREDICATE, "Person")]
    result = evaluate(plan, facts)
    assert set(result.facts) == _reference(plan.program, facts)
    assert not result.proofs.truncated
    assert Fact("alice", "hasUncle", "carol") in result
    assert Fact("dave", TYPE_PREDICATE, "Professor") in result
    assert Fact("erin", TYPE_PREDICATE, "Professor") not in result
    assert Fact("tom", TYPE_PREDICATE, "Pet") in result
    assert Fact("jerry", TYPE_PREDICATE, "Pet") in result
    assert Fact("mallory", "knows", "mallory") in result
    assert Fact("tom", "knows", "tom") not in result
    # Deterministic repeat with reversed input.
    assert evaluate(plan, list(reversed(facts))) == result


def test_member_forms_validate_and_canonicalize():
    assert IntersectionSubClassOf(["B", "A", "A"], "C").members == ("A", "B")
    assert UnionSubClassOf(["B", "A"], "C").members == ("A", "B")
    assert (IntersectionSubClassOf(["A", "B"], "C").axiom_id
            == IntersectionSubClassOf(["B", "A", "B"], "C").axiom_id)
    for make in [lambda: IntersectionSubClassOf([], "C"),
                 lambda: IntersectionSubClassOf(["A", ""], "C"),
                 lambda: IntersectionSubClassOf("AB", "C"),
                 lambda: UnionSubClassOf([], "C"),
                 lambda: UnionSubClassOf(["A"], ""),
                 lambda: PropertyChain("p", "", "q"),
                 lambda: PropertyChain(TYPE_PREDICATE, "p", "q"),
                 lambda: Reflexive("p", "")]:
        with pytest.raises(OntologyError):
            make()
    with pytest.raises(OntologyError, match="unsupported axiom"):
        compile_ontology(Ontology("o", [SubClassOf("A", "B"), "not-an-axiom"]))


def test_union_members_compile_to_distinct_origin_bound_rules():
    plan = compile_ontology(Ontology("o", [UnionSubClassOf(["A", "B"], "C")]))
    assert [rule.rule_id for rule in plan.program.rules] == [
        f"ontology:{plan.ontology.axioms[0].axiom_id}:union-0",
        f"ontology:{plan.ontology.axioms[0].axiom_id}:union-1",
    ]
    result = evaluate(plan, [Fact("n", TYPE_PREDICATE, "B")])
    assert Fact("n", TYPE_PREDICATE, "C") in result
    assert Fact("n", TYPE_PREDICATE, "A") not in result


def test_schema_cache_reuses_revisions_and_evicts_lru():
    from gestaltdb.ontology import CompiledSchemaCache

    with pytest.raises(OntologyError):
        CompiledSchemaCache(maxsize=0)
    cache = CompiledSchemaCache(maxsize=2)
    first = Ontology("o", [SubClassOf("A", "B")])
    assert cache.compile(first) is cache.compile(Ontology("o", [SubClassOf("A", "B")]))
    assert len(cache) == 1
    second = cache.compile(Ontology("o", [SubClassOf("A", "C")]))
    third = cache.compile(Ontology("o", [SubClassOf("A", "D")]))
    assert len(cache) == 2
    assert cache.compile(first) is not second and cache.compile(first).revision_id == first.revision_id
    assert third.program == cache.compile(Ontology("o", [SubClassOf("A", "D")])).program
    # Hit refresh decides the next eviction (recency, not insertion order).
    cache.clear()
    cache.compile(first)
    cache.compile(Ontology("o", [SubClassOf("A", "C")]))
    assert cache.compile(first) is cache.compile(Ontology("o", [SubClassOf("A", "B")]))
    cache.compile(Ontology("o", [SubClassOf("A", "E")]))
    assert len(cache) == 2
    assert cache.compile(first).revision_id == first.revision_id
    cache.clear()
    assert len(cache) == 0
    with pytest.raises(OntologyError):
        cache.compile("not-an-ontology")


def test_cached_plans_evaluate_identically():
    from gestaltdb.ontology import CompiledSchemaCache

    plan = CompiledSchemaCache().compile(Ontology("o", [PropertyChain("p", "q", "r")]))
    facts = [Fact("a", "p", "b"), Fact("b", "q", "c")]
    assert evaluate(plan, facts) == evaluate(compile_ontology(plan.ontology), facts)
    assert Fact("a", "r", "c") in evaluate(plan, facts)


def test_rdf_chain_intersection_union_and_reflexive_rejection():
    pytest.importorskip("rdflib")
    from gestaltdb.ontology.adapters.rdf import RDFDocument, import_rdf

    prefix = ('@prefix ex: <urn:ex:> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
              '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . ')
    doc = RDFDocument(prefix + """
        ex:hasUncle owl:propertyChainAxiom ( ex:hasParent ex:hasBrother ) .
        [ owl:intersectionOf ( ex:A ex:B ) ] rdfs:subClassOf ex:C .
        [ owl:unionOf ( ex:D ex:E ) ] rdfs:subClassOf ex:F .
        ex:G rdfs:subClassOf [ owl:intersectionOf ( ex:H ex:I ) ] .
        """)
    result = import_rdf(doc, ontology_id="urn:test")
    assert result.complete
    plan = result.compile()
    assert set(result.ontology.axioms) == {
        PropertyChain("urn:ex:hasParent", "urn:ex:hasBrother", "urn:ex:hasUncle"),
        IntersectionSubClassOf(("urn:ex:A", "urn:ex:B"), "urn:ex:C"),
        UnionSubClassOf(("urn:ex:D", "urn:ex:E"), "urn:ex:F"),
        SubClassOf("urn:ex:G", "urn:ex:H"),
        SubClassOf("urn:ex:G", "urn:ex:I"),
    }
    expected = compile_ontology(Ontology("o", list(result.ontology.axioms)))
    assert plan.program == expected.program
    assert evaluate(plan, [Fact("a", "urn:ex:hasParent", "b"),
                           Fact("b", "urn:ex:hasBrother", "c"),
                           Fact("x", TYPE_PREDICATE, "urn:ex:A"),
                           Fact("x", TYPE_PREDICATE, "urn:ex:B"),
                           Fact("y", TYPE_PREDICATE, "urn:ex:D")]).facts == evaluate(
        expected, [Fact("a", "urn:ex:hasParent", "b"),
                   Fact("b", "urn:ex:hasBrother", "c"),
                   Fact("x", TYPE_PREDICATE, "urn:ex:A"),
                   Fact("x", TYPE_PREDICATE, "urn:ex:B"),
                   Fact("y", TYPE_PREDICATE, "urn:ex:D")]).facts


@pytest.mark.parametrize("statement,match", [
    ("ex:p owl:propertyChainAxiom ( ex:q ) .", "exactly two"),
    ("ex:p owl:propertyChainAxiom ( ex:q ex:r ex:s ) .", "exactly two"),
    ("ex:A rdfs:subClassOf [ owl:unionOf ( ex:B ex:C ) ] .", "disjunctive superclass"),
    ("ex:p a owl:ReflexiveProperty .", "unscoped reflexivity"),
    ("[ owl:intersectionOf ( ex:A ) ] rdfs:subClassOf ex:C .", "at least two"),
    ("[] owl:intersectionOf ( ex:A ex:B ) .", "unused class collection"),
])
def test_rdf_strict_forms_reject_disjunctive_and_malformed_constructs(statement, match):
    pytest.importorskip("rdflib")
    from gestaltdb.ontology.adapters.rdf import RDFDocument, import_rdf

    prefix = ('@prefix ex: <urn:ex:> . @prefix owl: <http://www.w3.org/2002/07/owl#> . '
              '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> . ')
    with pytest.raises(OntologyError, match=match):
        import_rdf(RDFDocument(prefix + statement), ontology_id="urn:test")
    permissive = import_rdf(RDFDocument(prefix + statement), ontology_id="urn:test", strict=False)
    assert not permissive.complete
