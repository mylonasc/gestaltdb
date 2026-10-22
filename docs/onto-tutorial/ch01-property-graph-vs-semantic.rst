Property graphs vs semantic graphs
==================================

``src/gestaltdb/graphdb.py`` stores attributed ``Node`` / ``Edge`` records.
Labels are native; relationship type is the ``properties["type"]`` convention.
Semantic identity is *not* native: IRIs, ``rdf:type``, blank nodes and
entailment live one layer up, in ``src/gestaltdb/ontology/`` and
``src/gestaltdb/reasoning/``.

.. math::

   G_{prop} = (V, E, \lambda, \pi) \qquad
   G_{sem} = (T, \models) \;\; \text{with} \;\; T \subseteq S \times P \times O

A property edge ``Edge(properties={"type": "worksFor"})`` projects to a binary
fact ``Fact(subject, "worksFor", object)`` (``reasoning/model.py``). Class
membership projects ``(x, rdf{:}type, C)`` with
``TYPE_PREDICATE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"``.
The projection is explicit and caller-owned — see
:doc:`ontology-graph <../ontology-graph>`. Nothing is silently rewritten.

Parallels: LPG systems (Neo4j/Cypher, see :doc:`../cypher`) keep labels and
types as queryable data; RDF/OWL stores (RDFLib, OWL-RL, Jena) treat triples
plus entailment regimes as the data model. GestaltDB is an LPG store with an
*opt-in, offline* semantic overlay: ``ontology/adapters/rdf.py`` imports a
strict selected fragment and rejects the rest instead of approximating it.

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import PickleSerializer

   graph = GraphDB(LevelDBStore("/tmp/onto_tut_pg"), PickleSerializer())
   try:
       graph.put_node(Node(node_id="alice", labels=["Person"],
                           properties={"name": "Alice"}))
       graph.put_edge(Edge(edge_id="e1", source="alice", target="acme",
                           properties={"type": "worksFor"}))
       print(graph.query('MATCH (a:Person) RETURN a.name').records)
   finally:
       graph.close()
