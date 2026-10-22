Glossary: the distinctions that matter
======================================

Entity
   A thing with identity in the modeled domain: Alice, Acme Lab, or a paper.
   A node ID is an application's identifier for it, not a complete definition
   of what that entity means.

Relationship / predicate
   A named relation such as ``worksFor``. A stored property-graph edge has its
   own identity and properties; a binary logical fact is a subject/predicate/
   object tuple. Several physical edges may project to one set-valued fact.

Class / instance
   ``Researcher`` is a class; Alice is an instance. "Alice is a Researcher" is
   membership, while "Researcher is a subclass of Person" relates two classes.
   Those are different statements, not interchangeable edges.

Label / property
   A native node label supports grouping and lookup in the property graph.
   A property stores a value such as a display name. Neither gets OWL semantics
   merely by naming it after an ontology term; an explicit mapping is needed.

Graph database
   A database interface and storage/query system for graph-shaped data.
   Storage, indexing, transactions, and query execution are database concerns.

Knowledge graph
   A graph-based representation of entities and their relationships, intended
   to convey domain knowledge with identifiable terms and useful meaning.
   There is no single universally enforced definition. It may use property
   graphs or RDF, and need not have an OWL reasoner or a formal ontology.

RDF / IRI
   RDF represents a graph as a set of triples. IRIs identify terms; blank nodes
   and literals are also available in permitted positions. An IRI is not an
   instruction to fetch a URL. A local Python string is not automatically an IRI.

Ontology / axiom
   An ontology supplies explicit vocabulary and semantic statements. An axiom
   is one such statement, for example ``SubClassOf("Researcher", "Person")``.
   This tutorial uses a named, deliberately limited fragment, not all of OWL.

Assertion / consequence / closure
   An assertion is supplied as input. A consequence follows under the chosen
   rules. The closure is the asserted set plus all consequences in the declared
   fragment. Being in a logical closure does not imply being written to storage.

Query / inference / validation
   A query selects or computes an answer from a chosen view. Inference derives
   consequences under rules. Validation checks selected consistency or data
   requirements. A language or system can offer more than one of these services;
   domain/range inference is not a missing-field validation constraint.

Rule body / head / substitution
   In ``B1 ∧ B2 ⇒ H``, the body contains the premises and the head is the
   conclusion. A substitution consistently assigns constants to variables.
   Repeated variables must receive the same value throughout the rule.

Entailment / soundness / completeness
   :math:`F \cup P \models q` means that q follows from the input facts and rules under
   the declared semantics. Soundness excludes unjustified returned answers;
   completeness requires all answers of the supported fragment. Neither
   establishes that the input assumptions are true in the real world.

Fixed point / delta / intermediate representation
   A fixed point adds no new facts when the rules run again. A delta is the
   latest addition used to avoid repeating old join work. An intermediate
   representation (IR) expresses a task at a boundary—such as rules, plans,
   or compact relations—without forcing every stage into one data structure.

Open world / explicit negation / closed world
   Under an open-world reading, missing evidence is not evidence of falsehood.
   Explicit negative evidence is a separate assertion. A closed-world scope
   permits absence-based negation only within its declared boundaries.

Witness / support / provenance
   A witness is a selected explanation of a conclusion. A support records a
   particular rule/premise derivation; alternative supports matter on deletion.
   Provenance records where assertions or derivations came from and which
   context they belong to. One witness is not a complete maintenance index.

Snapshot / horizon / publication
   A snapshot pins what a read observes; a horizon identifies the selected
   system-time prefix. Publication is an explicit write of computed results.
   An array-native sampling snapshot is a different artifact from a database
   read snapshot or a logical closure.
