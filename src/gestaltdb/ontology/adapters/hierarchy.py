"""Explicit ontology-to-type-hierarchy adapters.

The hierarchy classes in :mod:`gestaltdb.hierarchy` are standalone and accept
plain external names. This adapter builds them from the direct subsumption
axioms of an immutable :class:`~gestaltdb.ontology.model.Ontology` so sampler
relation expansion and opt-in Cypher label matching can share the same declared
schema.

Only ``SubClassOf`` and ``SubPropertyOf`` are flattened. Symmetric equivalence
and intersection/union subsumption cannot be represented by a strict parent
hierarchy without choosing a canonical representative, so this adapter fails
closed when they are present. Callers must resolve those axioms explicitly or
construct the hierarchy directly.
"""

from __future__ import annotations

from ...hierarchy import LabelHierarchy, RelationHierarchy
from ..model import (
    EquivalentClasses,
    EquivalentProperties,
    IntersectionSubClassOf,
    Ontology,
    SubClassOf,
    SubPropertyOf,
    UnionSubClassOf,
)


_UNREPRESENTABLE = (
    EquivalentClasses,
    EquivalentProperties,
    IntersectionSubClassOf,
    UnionSubClassOf,
)


def _validate(ontology: Ontology) -> None:
    if not isinstance(ontology, Ontology):
        raise TypeError("ontology must be an Ontology")
    blocked = sorted({
        type(axiom).__name__
        for axiom in ontology.axioms
        if isinstance(axiom, _UNREPRESENTABLE)
    })
    if blocked:
        raise ValueError(
            "cannot build a strict hierarchy from ontology axioms that require "
            f"canonicalization: {blocked}"
        )


def label_hierarchy_from_ontology(ontology: Ontology) -> LabelHierarchy:
    """Build a node-label hierarchy from direct ``SubClassOf`` axioms.

    Multiple declared superclasses are preserved, and duplicate direct
    declarations are deduplicated by the hierarchy. Cycles and self-parenting
    fail closed at construction. Other non-subsumption axioms are ignored.
    """
    _validate(ontology)
    parents: dict[str, list[str]] = {}
    for axiom in ontology.axioms:
        if isinstance(axiom, SubClassOf):
            parents.setdefault(axiom.subclass, []).append(axiom.superclass)
    return LabelHierarchy(parents)


def relation_hierarchy_from_ontology(ontology: Ontology) -> RelationHierarchy:
    """Build a relation hierarchy from direct ``SubPropertyOf`` axioms.

    The contract matches :func:`label_hierarchy_from_ontology`: multiple
    superproperties are preserved, duplicates are deduplicated, and cycles or
    self-parenting fail closed.
    """
    _validate(ontology)
    parents: dict[str, list[str]] = {}
    for axiom in ontology.axioms:
        if isinstance(axiom, SubPropertyOf):
            parents.setdefault(axiom.subproperty, []).append(axiom.superproperty)
    return RelationHierarchy(parents)


__all__ = [
    "label_hierarchy_from_ontology",
    "relation_hierarchy_from_ontology",
]
