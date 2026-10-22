Property graphs vs semantic graphs
==================================

**Intuition first.** Picture a whiteboard: sticky notes for things
(``alice``, ``acme``), arrows between them labeled ``worksFor``. That is a
*property graph* — gestaltdb's home turf (``src/gestaltdb/graphdb.py``):
``Node`` objects carry native labels, ``Edge`` objects carry their
relationship in the ``properties["type"]`` convention. Now lay *tracing paper*
over the whiteboard and copy every arrow as a uniform triple
``(subject, predicate, object)``; write class membership as just another
arrow, ``(alice, rdf:type, Researcher)``. That tracing is the *semantic*
view. This tutorial's recurring diagram — built with gestaltdb's own viz
(``gestaltdb.viz.api`` → SVG export) from a real ``Node``/``Edge`` graph in
``toolkit/landscape.py`` — maps how the two views and their languages relate.
It grows in three stages; node color is the concept kind, arrow labels are
the relationships.

Stage 1 — the property-graph world
----------------------------------

.. figure:: figures/landscape-stage1.svg
   :width: 100%
   :alt: Stage 1: property graphs, Cypher patterns, GraphQL API resolvers, and SQL graph extensions

   Stage 1: the store and its query languages.

``Property Graph DB`` *stores* labeled nodes and typed edges. **Cypher**
expresses graph patterns (GestaltDB's query language; see :doc:`../cypher`).
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
               Node(node_id="alice", labels=["Researcher"],
                    properties={"name": "Alice"}),
               Node(node_id="acme", labels=["Organization"]),
           ])
           graph.put_edge(Edge(edge_id="e1", source="alice", target="acme",
                               properties={"type": "worksFor"}))
           rows = graph.query(
               'MATCH (a:Researcher) RETURN a.name AS name').records
           assert rows == [{"name": "Alice"}]
           assert not graph.query('MATCH (a:Person) RETURN a').records
       finally:
           graph.close()

The second assertion is the important one: without an explicit reasoning step,
the stored label ``Researcher`` does not imply a stored label ``Person``.
