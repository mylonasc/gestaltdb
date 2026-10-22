Querying and sampling with inferred facts
============================================

Inferred facts never rewrite the graph. Consumption is explicit:

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

   N_k(s) = \bigcup_{r \in R_{exp}} \text{sample}(\text{adj}_r(s), k_r)
   \qquad \sum_r k_r \leq K

Seed filtering alone never constrains every hop — define the eligible graph
with snapshot ``node_filter`` / ``edge_filter``. See :doc:`../typed-sampling`
and :doc:`../ontology-graph`. End-to-end pipeline glue is open work (#255,
#256).

Theory and further reading
---------------------------

- `SPARQL <https://en.wikipedia.org/wiki/SPARQL>`_ (`SPARQL 1.1 overview
  <https://www.w3.org/TR/sparql11-overview/>`_) shows the alternative design:
  entailment regimes baked into the query language itself. GestaltDB goes the
  other way — plain Cypher over stored facts plus explicit adapters — so that
  inferred and stored facts never mix silently.
- `Graph query language
  <https://en.wikipedia.org/wiki/Graph_query_language>`_ compares the
  traversal idioms (pattern matching, path queries, fanout control) that both
  Cypher clauses and ``expand_edge_types`` sampling implement in their own
  layer.
