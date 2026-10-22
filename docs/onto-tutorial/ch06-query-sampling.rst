Querying and sampling with inferred facts
============================================

Evaluation alone does not rewrite the graph. Consumption or publication is explicit:

* ``reasoning/adapters/graph.py`` — ``GraphMapping`` + ``expand_edge_types``
  projects inferred predicates to typed traversals with per-relation fanout
  accounting; ``sample_expanded_neighbors`` samples without changing arrays.
* Cypher (``graph.query``): no automatic inferred-pattern rewrites. Opt-in
  call ``pg.sample_typed_paths`` / ``kg.entails`` stay allowlisted procedures.
* ``SamplerSnapshot`` / ``SamplerEngine`` (``sampling/``): compact integer
  node/edge/relation IDs, RAM/memmap modes, temporal v2 with point/window
  filters and causal policies; ``node_ids_global`` maps local batch rows back
  to snapshot IDs.

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

Seed filtering alone never constrains every hop — define the eligible graph
with snapshot ``node_filter`` / ``edge_filter``. See :doc:`../typed-sampling`
and :doc:`../ontology-graph`. End-to-end pipeline glue is open work (#255,
#256).

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
