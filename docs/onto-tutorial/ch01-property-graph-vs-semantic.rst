Graph databases, knowledge graphs, and meaning
==============================================

Why store a graph?
-------------------

Suppose an application asks "Which people work at organizations connected to
this research group?" The interesting part is the connections, not one record
in isolation. A graph makes those connections explicit: entities are nodes,
relationships are edges, and a path joins several relationships into a route.
Direction matters: Alice working for Acme does not mean Acme works for Alice.

A graph database stores and queries such structures. That does not make it
automatically a **knowledge graph**. The latter describes the *representation
of domain knowledge*: which things identifiers denote, what relationship names
mean, and how statements can be combined or interpreted. The term has several
usages; a knowledge graph need not use RDF, a formal ontology, or any inference
engine. A property graph can represent one, as can RDF stored in a triple store.

Keep these questions separate:

.. list-table:: Three layers, three responsibilities
   :header-rows: 1
   :widths: 22 41 37

   * - Layer
     - Question
     - In our example
   * - Graph model
     - What are the entities and connections?
     - Alice ``worksFor`` Acme.
   * - Database
     - How are they stored and retrieved consistently?
     - Persist an edge; query it through Cypher.
   * - Domain semantics
     - What follows from the chosen meaning?
     - Employment implies Employee membership, not a research role.

SQL tables can encode these same relationships using keys and joins. A graph
database does not create information unavailable to a relational representation,
nor is every graph query inherently faster. It changes the interface and storage
choices around relationship-heavy workloads. A reasoning engine adds another
service: consequences of explicit rules, independent of the storage format.

Choosing nodes, edges, labels, and properties
---------------------------------------------

Give something a node when its identity and connections matter: Alice and Acme
must be recognizable across many statements. Give ``Alice`` a display-name
property rather than using the spelling as identity; two people can share that
name. ``Employee`` can be a label for storage/query purposes. Treating it as
an ontology class requires an explicit mapping, not a suggestive string.

The same choice affects facts about relationships. A physical ``worksFor`` edge
can have an ID, a source, a start date, and a role. Collapsing it to a logical
triple loses those distinctions unless they are modeled separately. If two
employment records have the same endpoints, they are two records but may
support the same proposition. This distinction will matter for provenance and
deletion in later chapters.

The technology landscape
-------------------------

**Intuition first.** Picture a whiteboard: sticky notes for things
(``alice``, ``acme``), arrows between them labeled ``worksFor``. That is a
*property graph* — gestaltdb's home turf (``src/gestaltdb/graphdb.py``):
``Node`` objects carry native labels, ``Edge`` objects carry their
relationship in the ``properties["type"]`` convention. Now lay *tracing paper*
over the whiteboard and copy every arrow as a uniform triple
``(subject, predicate, object)``; write class membership as just another
arrow, ``(alice, rdf:type, Employee)``. That tracing is the *semantic*
view. In the following maps, colors and enclosing groups distinguish conceptual
layers; small badges describe each node's role. Read each arrow as a labeled
relationship, not as an unconditional data-conversion pipeline. The picture
grows in three stages so it need not be learned all at once.

Stage 1 — the property-graph world
----------------------------------

.. figure:: figures/landscape-stage1.svg
   :width: 100%
   :alt: Stage 1: property graphs, Cypher patterns, GraphQL API resolvers, and SQL graph extensions

   Stage 1: graph storage and related language/API families.

``Property Graph DB`` *stores* labeled nodes and typed edges. **Cypher**
expresses graph patterns (GestaltDB's query language; see the library's
`Cypher reference <https://github.com/mylonasc/gestaltdb/blob/main/docs/cypher.rst>`_).
**GraphQL** describes a typed API response: application resolvers may read a
graph, a relational database, or remote services. It is not a native property-
graph query language. **SQL** queries relations; **SQL/PGQ** adds property-graph
pattern matching to SQL implementations that support it. GestaltDB supplies
neither SQL/PGQ nor a GraphQL server. The map compares technologies, not a list
of supported endpoints.

Intuition: a query language is a way to ask about the whiteboard; an API is a
way to package answers. Naming an arrow ``worksFor`` does not by itself add
domain, range, or subclass inference.

Stage 2 — the semantic overlay
------------------------------

.. figure:: figures/landscape-stage2.svg
   :width: 100%
   :alt: Stage 2: RDF triples, OWL, and the gestalt-core-v1 ontology fragment

   Stage 2: projecting the whiteboard onto tracing paper.

Selected property nodes/edges *project to* binary facts with an explicit
mapping. **RDF** represents graphs as sets of triples: subjects are IRIs or
blank nodes, predicates are IRIs, and objects may also be literals. A bare
``Fact("alice", "worksFor", "acme")`` is a logical triple over local symbols,
not automatically a standards-compliant RDF serialization. Export would need
an IRI policy. Parallel edges collapse under set semantics unless their
identities are modeled separately.

**OWL** provides an ontology language, with an RDF serialization, for saying
what relationships mean
(subclasses, domains, inverses, transitivity). A gestaltdb **Ontology**
*selects from* OWL a small decidable fragment (``PROFILE =
"gestalt-core-v1"``) and *contains* **Axioms** — see
:doc:`ch02-ontology-fragment`. Intuition: the tracing paper doesn't replace
the whiteboard; it is an explicit, lossy-but-uniform copy. The projection is
caller-owned. ``reasoning/adapters/graph.py`` maps current graph records to
facts. In contrast, ``ontology/adapters/rdf.py`` imports selected *schema
axioms* from RDF documents; it is not a general instance-data (ABox) loader.
Strict import rejects unsupported constructs. Non-strict import explicitly
marks the report incomplete and requires separate consent before compilation.

Stage 3 — the reasoning layer
-----------------------------

.. figure:: figures/landscape-stage3.svg
   :width: 100%
   :alt: Stage 3: Datalog/Prolog rules compiled from axioms and evaluated by DatalogEngine

   Stage 3: an assistant that fills in the missing arrows.

.. only:: html

   :download:`Open the full-resolution overview <figures/landscape-stage3.svg>`
   to zoom into individual relationships. The complete map is an overview;
   stages 1–2 above provide less crowded views of the foundational layers.

Axioms *compile to* **Rules** over **Facts**; **Datalog** *defines* that rule
shape (safe, function-free Horn rules — see :doc:`ch03-datalog-irs`).
**Prolog** is a related logic-programming language, typically evaluated by
goal-directed search with clause order, compound terms, and optional control
features. It is not simply Datalog with more keywords.
``DatalogEngine`` evaluates facts or evidence supplied by callers; explicit
adapters can obtain those from current records or temporal claims. The engine
does not read a database or construct a ``SamplerSnapshot`` itself. Passing
inferred endpoints as sampler seeds does not make later hops traverse inferred
edges.

**Cypher** and Datalog both bind variables by matching relations, but their
contracts differ: Cypher returns query rows (and can perform writes), whereas
positive Datalog computes a set-valued closure. Intuition: the engine reads the tracing paper
plus the rulebook and pencils in every arrow the rules demand (``alice`` is a
``Person``), while the whiteboard itself stays untouched.

Parallels: LPG systems (Neo4j/Cypher) keep labels and types as queryable
data; RDF/OWL stores (RDFLib, OWL-RL, Jena) treat triples plus entailment
regimes as the data model. GestaltDB is an LPG store with an opt-in, offline
semantic overlay — nothing is silently rewritten.

Theory and further reading
---------------------------

- `Graph database <https://en.wikipedia.org/wiki/Graph_database>`_ and
  `Graph query language <https://en.wikipedia.org/wiki/Graph_query_language>`_
  survey the LPG/RDF split and the language families (Cypher, Gremlin,
  SPARQL) that grew around each side.
- `Cypher <https://en.wikipedia.org/wiki/Cypher_(query_language)>`_ is
  pattern matching over stored structure; `SPARQL
  <https://en.wikipedia.org/wiki/SPARQL>`_ is the W3C counterpart over
  triple stores (`SPARQL 1.1 overview <https://www.w3.org/TR/sparql11-overview/>`_).
  GestaltDB deliberately exposes Cypher but no SPARQL endpoint — inferred
  triples are consumed through explicit adapters, not a second query language.
- `Resource Description Framework
  <https://en.wikipedia.org/wiki/Resource_Description_Framework>`_ (`RDF 1.1
  Concepts <https://www.w3.org/TR/rdf11-concepts/>`_) defines the triple
  model gestaltdb projects onto; the `Semantic Web
  <https://en.wikipedia.org/wiki/Semantic_Web>`_ article gives the original
  vision (machine-readable web of data) that OWL and the ontology layer come
  from.
- `GraphQL <https://en.wikipedia.org/wiki/GraphQL>`_ specifies client-shaped
  API selection; its resolvers choose how and where to fetch the data.

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from tempfile import TemporaryDirectory

   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([
               Node(node_id="alice", labels=["Employee"],
                    properties={"name": "Alice"}),
               Node(node_id="acme", labels=["Organization"]),
           ])
           graph.put_edge(Edge(edge_id="e1", source="alice", target="acme",
                               properties={"type": "worksFor"}))
           rows = graph.query(
               'MATCH (a:Employee) RETURN a.name AS name').records
           assert rows == [{"name": "Alice"}]
           assert not graph.query('MATCH (a:Person) RETURN a').records
       finally:
           graph.close()

The second assertion is the important one: without an explicit reasoning step,
the stored label ``Employee`` does not imply a stored label ``Person``.
The first query returns exactly ``[{"name": "Alice"}]``; the second returns no
rows. No fact about Alice's identity is missing—we have not yet told a reasoner
what ``Employee`` means. Neither label lookup nor employment alone establishes
that Alice is a researcher.

Check your understanding
-------------------------

**Question.** If Alice's node has label ``Employee``, why doesn't a normal
``MATCH (a:Person)`` return it? Is this a failure of graph storage?

**Answer.** The query matches a different stored label. Subclass meaning and
the decision to consult inferred membership are separate from label lookup.
The database is doing the requested lookup correctly. Chapter 2 supplies the
meaning; chapter 4 connects it to stored records without changing the labels.
