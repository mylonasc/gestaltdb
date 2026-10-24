"""PropEnh-14 (#127): ontology axioms to explicit sampling/Cypher hierarchies."""

import pytest

from gestaltdb.hierarchy import LabelHierarchy, RelationHierarchy
from gestaltdb.ontology import (
    Domain,
    EquivalentClasses,
    EquivalentProperties,
    IntersectionSubClassOf,
    Ontology,
    SubClassOf,
    SubPropertyOf,
    UnionSubClassOf,
)
from gestaltdb.ontology.adapters.hierarchy import (
    label_hierarchy_from_ontology,
    relation_hierarchy_from_ontology,
)


def _ontology(*axioms):
    return Ontology("urn:test:hierarchy", axioms)


def test_label_hierarchy_uses_direct_subclass_axioms():
    ontology = _ontology(
        SubClassOf("Ocean_Freight", "Transport_Edge"),
        SubClassOf("Refrigerated_Freight", "Ocean_Freight"),
        SubClassOf("Amphibious", "Land"),
        SubClassOf("Amphibious", "Water"),
        Domain("Ocean_Freight", "Freight"),
    )
    hierarchy = label_hierarchy_from_ontology(ontology)
    assert hierarchy.closure("Transport_Edge") == (
        "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight")
    assert hierarchy.parents_of("Amphibious") == ("Land", "Water")
    assert hierarchy.closure("Land") == ("Land", "Amphibious")
    assert "Ocean_Freight" in hierarchy and "Missing" not in hierarchy


def test_relation_hierarchy_uses_direct_subproperty_axioms():
    ontology = _ontology(
        SubPropertyOf("Ocean_Freight", "Transport_Edge"),
        SubPropertyOf("Rail", "Transport_Edge"),
        SubPropertyOf("Refrigerated_Freight", "Ocean_Freight"),
        SubClassOf("Ocean_Freight", "Transport_Edge"),
    )
    hierarchy = relation_hierarchy_from_ontology(ontology)
    assert hierarchy.expand(["Transport_Edge"]) == (
        "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight", "Rail")
    assert isinstance(hierarchy, RelationHierarchy)
    # Class-only axioms do not leak into relation expansion.
    assert hierarchy.closure("Rail") == ("Rail",)


def test_class_and_property_hierarchies_are_independent():
    ontology = _ontology(
        SubClassOf("Child", "Parent"),
        SubPropertyOf("child_edge", "parent_edge"),
    )
    labels = label_hierarchy_from_ontology(ontology)
    relations = relation_hierarchy_from_ontology(ontology)
    assert labels.closure("Parent") == ("Parent", "Child")
    assert labels.closure("parent_edge") == ("parent_edge",)
    assert relations.closure("parent_edge") == ("parent_edge", "child_edge")
    assert relations.closure("Parent") == ("Parent",)


def test_empty_ontology_builds_empty_hierarchies():
    ontology = _ontology()
    labels = label_hierarchy_from_ontology(ontology)
    relations = relation_hierarchy_from_ontology(ontology)
    assert len(labels) == 0 and len(relations) == 0
    assert labels.closure("Anything") == ("Anything",)
    assert relations.closure("anything") == ("anything",)


def test_wrong_input_type_fails_closed():
    with pytest.raises(TypeError, match="Ontology"):
        label_hierarchy_from_ontology({"SubClassOf": []})
    with pytest.raises(TypeError, match="Ontology"):
        relation_hierarchy_from_ontology(object())


@pytest.mark.parametrize("axiom", [
    EquivalentClasses("A", "B"),
    EquivalentProperties("a", "b"),
    IntersectionSubClassOf(("A", "B"), "C"),
    UnionSubClassOf(("A", "B"), "C"),
])
def test_unrepresentable_axioms_fail_closed(axiom):
    ontology = _ontology(axiom)
    with pytest.raises(ValueError, match="require canonicalization"):
        label_hierarchy_from_ontology(ontology)
    with pytest.raises(ValueError, match="require canonicalization"):
        relation_hierarchy_from_ontology(ontology)


def test_cycles_and_self_parenting_fail_closed():
    cyclic = _ontology(
        SubClassOf("A", "B"),
        SubClassOf("B", "A"),
    )
    with pytest.raises(ValueError, match="cycle"):
        label_hierarchy_from_ontology(cyclic)

    cyclic_relations = _ontology(
        SubPropertyOf("a", "b"),
        SubPropertyOf("b", "a"),
    )
    with pytest.raises(ValueError, match="cycle"):
        relation_hierarchy_from_ontology(cyclic_relations)

    self_parent = _ontology(SubClassOf("A", "A"))
    with pytest.raises(ValueError, match="cannot parent itself"):
        label_hierarchy_from_ontology(self_parent)


def test_duplicate_declarations_are_deduplicated():
    ontology = _ontology(
        SubClassOf("Child", "Parent"),
        SubClassOf("Child", "Parent"),
    )
    hierarchy = label_hierarchy_from_ontology(ontology)
    assert hierarchy.parents_of("Child") == ("Parent",)
    assert hierarchy.closure("Parent") == ("Parent", "Child")
    assert isinstance(hierarchy, LabelHierarchy)
