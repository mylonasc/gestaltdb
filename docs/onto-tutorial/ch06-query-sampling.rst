Querying and sampling with inferred facts
============================================

Which graph does an answer come from?
--------------------------------------

We can now answer whether Alice is a person, but the application still needs to
choose *which view it is asking*. Chapter 4 deliberately produced two different
results: the logical closure contained Person membership, while the stored-label
query did not. Neither answer was a bug. They answered questions about different
representations.

For a stored-graph question, use Cypher to match physical records, labels, and
paths. For a consequence under the chosen ontology, query the completed logical
result, such as ``logical.match(subject="alice", predicate=TYPE_PREDICATE)``
in chapter 4. For a persisted derived view, explicitly publish results through
the supported publication workflow. These are three distinct choices; evaluating
a program does not secretly make future Cypher queries inference-aware.

This matters especially for counts. Two employment records can have separate
edge IDs, one logical proposition, several independent supports, and many paths
through a class hierarchy. Counting edges, facts, supports, or paths can therefore
give different numbers. Choose the unit of your question before choosing a
query API.

Why sampling is a different operation
--------------------------------------

A directory endpoint may need every person satisfying a predicate. A graph
learning pipeline may instead need a bounded neighborhood around selected
entities. Sampling exchanges complete enumeration for a selection policy and
a fanout budget. It must state whether the population consists of physical
edges, distinct endpoints, or paths: a high-degree endpoint with many proofs
should not acquire a larger weight accidentally.

GestaltDB's explicit logical adapters can inspect inferred relations and sample
distinct endpoints. ``expand_edge_types`` identifies mapped relation members;
``sample_expanded_neighbors`` accounts for their contributions under a total
fanout cap. For a seed ``s``, let ``N_r(s)`` be the endpoint set for member
relation ``r`` and ``k_r`` its quota:

.. math::

   N_K(s) = \operatorname{distinct}\!\left(
       \bigcup_{r \in R_{exp}} \operatorname{sample}(N_r(s), k_r)\right),
   \qquad \sum_r k_r = K, \quad |N_K(s)| \leq K

For ``sample_expanded_neighbors``, ``N_r(s)`` is the distinct logical endpoint
set for an expanded member relation, not a bucket of physical edge IDs. Quotas
split ``K`` evenly in sorted relation order, with earlier members taking the
remainder. Overlapping endpoints are attributed to the first member; unused
quota is not redistributed. Thus fanout 10 is a cap, not a guarantee of ten
distinct endpoints or uniform sampling over the union. Multiple proof paths
do not increase an endpoint's weight. Inferred seed handoff leaves the next
asserted-graph sampling hop unchanged.

The array-native ``SamplerSnapshot``/``SamplerEngine`` serves repeated training
workloads. It trades repeated KV reads for reusable compact arrays and relation
indexes. Those arrays are a different artifact from a logical closure. Passing
inferred endpoint IDs as seeds selects *where to start* in the snapshot; it does
not add inferred edges to the snapshot or change what later hops traverse.
External entity IDs also differ from compact snapshot integers and local batch
rows. Use the documented mappings rather than joining them by numerical value.

There is a similar trap with filtering. Choosing Alice as an eligible seed does
not ensure every intermediate node belongs to a desired cohort. To constrain
the sampled graph, define snapshot ``node_filter``/``edge_filter`` as appropriate,
not just a seed list. The supported logical and array-native workflows are
described separately in the library's
`graph adapter <https://github.com/mylonasc/gestaltdb/blob/main/docs/ontology-graph.rst>`_
and `sampling <https://github.com/mylonasc/gestaltdb/blob/main/docs/typed-sampling.rst>`_ guides.

Theory and further reading
---------------------------

- `SPARQL <https://en.wikipedia.org/wiki/SPARQL>`_ uses basic graph pattern
  matching by default. The separate `SPARQL 1.1 Entailment Regimes
  <https://www.w3.org/TR/sparql11-entailment/>`_ recommendation defines
  extensions for selected entailment relations. A SPARQL endpoint does not
  automatically implement OWL inference. GestaltDB exposes Cypher plus explicit
  reasoning adapters, not a SPARQL endpoint.
- `Graph query language
  <https://en.wikipedia.org/wiki/Graph_query_language>`_ compares the
  traversal idioms (pattern matching, path queries, fanout control) that both
  Cypher clauses and ``expand_edge_types`` sampling implement in their own
  layer.

Check your understanding
-------------------------

**Question.** We use inferred people as seeds for a snapshot built from stored
edges. Does the sampler's next hop automatically traverse inferred relations?
Does fanout 10 necessarily return ten distinct neighbors?

**Answer.** No. A seed handoff does not change snapshot topology. Fanout is a
cap; member quotas, overlap, and sparse populations can return fewer endpoints.
Neither a proof count nor a parallel-edge count is implicitly a sampling weight.
