Current-graph ontology adapters
========================================

The explicit adapter ``gestaltdb.reasoning.adapters.graph`` projects one
verifiable mutable-state read snapshot. It never reads temporal claims and
does not alter Cypher planning, stored labels/edges, typed adjacency or sampler
arrays. The Datalog engine imports none of these integrations.

For indexed goal-directed reads over this same mapping and snapshot contract,
see :doc:`reasoning-current-frontier`. Full projection remains available below.
For complete projection with shared generation pins and explicit executor
admission, see :doc:`reasoning-current-admission`.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node, Edge
   from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.adapters.graph import GraphMapping, PinnedGraphSource

   plan = compile_ontology(Ontology("relations", [SubPropertyOf("knows", "related")]))
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("a"), Node("b")])
           graph.put_edge(Edge("ab", "a", "b", {"type": "KNOWS"}))
           with graph.current_read_view(require_verifiable=True) as view:
               with PinnedGraphSource(view, mapping=GraphMapping(
                   edge_types=[("KNOWS", "knows")],
               )) as source:
                   scoped = source.evaluate(plan, limits=ReasoningLimits())
           assert scoped.sample_neighbors("a", predicate="related", direction="out",
                                          fanout=10, seed=1) == ("b",)
       finally:
           graph.close()

Mapping and query semantics
---------------------------

``GraphMapping(labels=[(label, class_iri)], edge_types=[(type, predicate)],
node_properties=[(property_key, predicate)])`` is an allowlist. Unmapped fields
are ignored. Property values must be single nonempty entity-ID strings; literal
coercion, list expansion and edge-attribute statements are not implicit. Node and
endpoint IDs remain unchanged. Named labels become RDF type facts. Dangling
endpoint IDs are logical constants, not automatically created graph nodes.

``scoped.match(...)`` filters complete logical triples, not graph entities or
Cypher rows. ``scoped.result.explain(fact)`` works if proofs were collected.
The immutable result can outlive its read view; extraction cannot. Keep the
view open through evaluation and close sources explicitly. Unsupported backend
snapshot identities fail closed. ``GraphSourceLimits.max_records`` bounds source
records; kernel bounds independently constrain projected facts/closure. Neither
bounds the full-state snapshot authentication cost.

Expanded-relation fanout
----------------------------------------

``sample_neighbors`` requires an explicit derived predicate and direction.
It first selects distinct logical endpoints from the completed closure, then
samples uniformly without replacement with a local seeded RNG. Parallel edges,
multiple inheritance routes and independent proofs do not multiply weights.
Fanout zero returns no endpoints; fanout above population returns all endpoints.
Self loops can appear if logically entailed. Results are sorted external IDs,
not compact sampler IDs, physical edge IDs or paths. A caller can use selected
IDs as seeds for a separate asserted-graph sampler, but that sampler's next hop
does not automatically use inferred relations. There is no implicit multi-hop,
weighted sampling or Cypher clause rewrite. This explicit semantic boundary is
the integration point for the broader hierarchical-sampling work in issue #127.

Typed n-ary projection (#396)
----------------------------------------

``gestaltdb.reasoning.adapters.typed_graph`` projects the same snapshot
contract to ``TypedFact`` values for the generic engine. Beyond the v1
entity-only fields, ``TypedGraphMapping`` coerces scalar attributes to
``Literal`` values (``str``/``int``/``float``/``bool``; anything else fails
closed), expands list values element-wise under ``TypedSourceLimits``
(one fact per element; overruns fail closed, never truncate), and projects
edge attributes as n-ary ``predicate(source, target, value)`` facts.
``node_identity=[(label, property)]`` renames node subjects to a property
value; edge endpoints resolve through the same record-to-subject map inside
``PinnedTypedGraphSource``. Two distinct records mapping to one subject is an
identity collision: the default ``identity_collision="error"`` fails closed and
names both records. Pass ``identity_collision="merge"`` only for intentional
entity resolution; facts then merge, while each projected assertion keeps its
own source reference and no logical equivalence is asserted. Every projected
fact carries an unambiguous JSON reference such as
``typed:node-attribute:["a","name"]`` (list elements append an index); because
components are encoded separately, IDs and property names containing ``:``
cannot alias. ``source.context(revision)`` pins the v3 ``mapping_id``,
including the collision policy, so a mapping change yields a new generation
token and stale caches cannot match.

.. code-block:: python

   from gestaltdb.graphdb import GraphDB, Node, Edge
   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.adapters.typed_graph import PinnedTypedGraphSource, TypedGraphMapping
   from gestaltdb.reasoning.terms import Literal
   from gestaltdb.reasoning.typed import TypedFact
   from tempfile import TemporaryDirectory

   mapping = TypedGraphMapping(node_attributes=[("age", "age")],
                               edge_attributes=[("amount", "amount")])
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("a", properties={"age": 30})])
           graph.put_edge(Edge("ab", "a", "b", {"type": "PAYS", "amount": 5}))
           with graph.current_read_view(require_verifiable=True) as view:
               with PinnedTypedGraphSource(view, mapping=mapping) as source:
                   evidence = list(source.iter_evidence())
           facts = [item.fact for item in evidence]
           assert TypedFact("age", ["a", Literal("integer", 30)]) in facts
           assert TypedFact("amount", ["a", "b", Literal("integer", 5)]) in facts
       finally:
           graph.close()
