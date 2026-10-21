"""Immutable named-entity ontology axioms and standalone rule compilation.

Import axioms from ``gestaltdb.ontology.model`` and ``compile_ontology`` from
``gestaltdb.ontology.compiler``. This is the selected ``gestalt-core-v1``
fragment, not full RDFS or OWL support. No graph catalog is read or changed.
"""

from .compiler import CompiledOntology, RuleOrigin, compile_ontology
from .model import (
    Domain, EquivalentClasses, EquivalentProperties, IntersectionSubClassOf, InverseOf, Ontology,
    OntologyError, PROFILE, PropertyChain, Range, Reflexive, SubClassOf, SubPropertyOf, Symmetric,
    Transitive, UnionSubClassOf, TYPE_PREDICATE,
)

__all__ = [
    "CompiledOntology", "RuleOrigin", "compile_ontology", "Ontology", "OntologyError",
    "PROFILE", "TYPE_PREDICATE", "SubClassOf", "SubPropertyOf", "EquivalentClasses",
    "EquivalentProperties", "Domain", "Range", "InverseOf", "Symmetric", "Transitive",
    "PropertyChain", "IntersectionSubClassOf", "UnionSubClassOf", "Reflexive",
]
