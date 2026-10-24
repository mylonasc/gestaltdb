Connecting a database to a reasoner
====================================

Why the engine should not read an arbitrary live graph
------------------------------------------------------

Chapter 3 reasoned over a Python list of facts. Real applications do not start
with that list: they start with stored nodes, labels, properties, and edges.
The first integration problem is deciding which stored fields mean which
logical predicates. An edge called ``worksFor`` is an application convention;
the engine should not guess that every string property is an entity relation.

The second problem is consistency. Suppose an appointment is read before a
correction but its related organization is read afterward. The assembled input
could describe no state that ever existed. Correct rule evaluation would then
produce a logically consistent answer about an inconsistent extraction. Pinning
a read snapshot prevents that kind of moving target where the backend supports
the required guarantee.

GestaltDB therefore makes the boundary explicit. A mapping selects fields;
a read view pins the source; an adapter supplies logical facts and evidence;
the evaluator computes consequences. The evaluator itself stays independent of
storage, so the same program can be tested over a small list or evaluated over
an extracted graph without changing its meaning.

A complete stored-graph example
--------------------------------

We now deliberately store Alice **without** an ``Employee`` or ``Person``
label. The only relevant assertion is the appointment edge. The ontology from
chapter 2 will supply both memberships. Follow the four steps in order: store,
map, evaluate, and compare the logical answer with the unchanged stored graph.

This particular example requires a verifiable current snapshot and therefore
uses LMDB. :doc:`backends` explains why LevelDB and default PyRex cannot replace
it, and gives a portable basic graph lab for all three backends.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.ontology import (
       Domain, Ontology, SubClassOf, TYPE_PREDICATE, compile_ontology,
   )
   from gestaltdb.reasoning import Fact, ReasoningLimits
   from gestaltdb.reasoning.adapters.graph import GraphMapping, PinnedGraphSource

   compiled = compile_ontology(Ontology("employment", [
       Domain("worksFor", "Employee"), SubClassOf("Employee", "Person"),
   ]))
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("alice"), Node("acme")])
           graph.put_edge(Edge("appointment", "alice", "acme", {"type": "worksFor"}))
           mapping = GraphMapping(edge_types=[("worksFor", "worksFor")])
           with graph.current_read_view(require_verifiable=True) as view:
               with PinnedGraphSource(view, mapping=mapping) as source:
                   logical = source.evaluate(
                       compiled, limits=ReasoningLimits(), collect_proofs=True)
           person = Fact("alice", TYPE_PREDICATE, "Person")
           assert logical.match(subject="alice", predicate=TYPE_PREDICATE) == tuple(sorted([
               person, Fact("alice", TYPE_PREDICATE, "Employee"),
           ]))
           assert not logical.match(subject="alice", predicate=TYPE_PREDICATE,
                                    object="Researcher")
           assert not logical.result.explain(person).truncated
           assert graph.get_node(b"alice").labels == ()
           assert not graph.query("MATCH (a:Person) RETURN a").records
       finally:
           graph.close()

The mapping's first ``worksFor`` is a physical edge type; the second is the
logical predicate. They happen to have the same spelling, but need not. This
is an allowlist: unmapped fields are ignored. A label-to-class mapping could
also supply membership assertions. Projection uses set-valued facts, so several
physical edges can support the same logical statement without multiplying
that statement in the closure.

The two membership assertions succeed, while the label query still returns
no rows. That is the point of the exercise. An in-memory reasoning result is
not a hidden write to the database. The immutable result may outlive the read
view; extraction must finish while the view remains open. No Researcher
membership follows from employment alone; the additional rule in chapter 2
would also need a mapped job-description fact. Notebook 3 adds a
visualization of this caller-owned logical result.

When results should become durable
-----------------------------------

An application might reuse the inferred memberships in many reports. It could
keep the result in memory, recompute it, or explicitly publish a materialized
generation. Publication is a different operation from evaluation: it creates
persisted derived claims with a source context and a lifecycle.

That separation matters when writers are active. Imagine computing Alice's
membership, then publishing it after the appointment was retracted. A staged
publication must compare its source/target identities with the state it is
allowed to publish against; a stale stage needs an explicit retry rather than
quietly becoming current. Recovery after an interrupted nontransactional write
also requires an audit policy, not an assumption that every artifact is valid.
See the library's `generation <https://github.com/mylonasc/gestaltdb/blob/main/docs/reasoning-generations.rst>`_
and `publication <https://github.com/mylonasc/gestaltdb/blob/main/docs/ontology-publication.rst>`_ guides for the
point-publication contracts; they are not implicit behavior of ``evaluate``.

Why extraction can dominate performance
----------------------------------------

An excellent join plan cannot recover time already spent decoding every record
or authenticating a snapshot. Bounded extraction batches limit an interface's
batch size, not necessarily total process memory or the authentication cost.
Native KV scans can reduce some overhead, but raw bytes still need decoding
and visibility checks before they become evidence. Backend capabilities differ;
an adapter must reject an unsupported requested guarantee rather than quietly
substitute a weaker read.

For this reason the example chooses LMDB explicitly. It does not assume that
every backend offers the same verifiable current snapshot. The deeper APIs are
documented in the library's `graph adapter <https://github.com/mylonasc/gestaltdb/blob/main/docs/ontology-graph.rst>`_
and `extraction <https://github.com/mylonasc/gestaltdb/blob/main/docs/reasoning-extraction.rst>`_ guides.

Check your understanding
-------------------------

**Question.** The logical result contains Alice's Person membership. Why does
the final Cypher label query still return nothing? Could we safely re-run
extraction after closing the read view?

**Answer.** The result was not published as a stored label, and plain Cypher
does not automatically consult it. Extraction borrows the open pinned view;
only the completed immutable result can be used independently afterward.
