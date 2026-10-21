:orphan:

Explicit relation expansion and sampler handoff
============================================================

``gestaltdb.reasoning.adapters.graph`` gains explicit inferred-query helpers:
``expand_edge_types`` resolves which concrete mapped edge types can derive a
predicate, ``sample_expanded_neighbors`` samples distinct endpoints with
per-relation fanout accounting, and ``inferred_seeds`` validates inferred
endpoints into sampler seed lists. Nothing rewrites Cypher, mutates arrays,
or changes weight/path multiplicity.

.. code-block:: python

   from gestaltdb.reasoning.adapters.graph import (
       GraphMapping, expand_edge_types, inferred_seeds, sample_expanded_neighbors,
   )

   mapping = GraphMapping(edge_types=[("narrowEdge", "narrow"), ("wideEdge", "wide")])
   members = expand_edge_types("wide", compiled, mapping)  # ("narrowEdge", "wideEdge")
   expanded = sample_expanded_neighbors(scoped, "a", predicate="wide", direction="out",
                                        fanout=10, seed=7, mapping=mapping, compiled=compiled)
   seeds = inferred_seeds(expanded.endpoints)  # feed to sampler seed APIs explicitly

Expansion, not rewriting
----------------------------------------

Expansion follows compiled positive rule dependencies backwards and keeps only
mapped edge types. Unknown predicates fail closed instead of silently
expanding to nothing. The caller names the derived predicate explicitly; no
query text is rewritten and no hidden type expansion happens inside
``sample_neighbors``.

Fanout accounting
----------------------------------------

The fanout budget splits as evenly as possible across member relations in
sorted order (earlier members take the remainder); each member samples
uniformly without replacement up to its quota. Endpoints are attributed to
the first member that produced them, so totals never exceed ``fanout``.
``ExpandedNeighborhood.contributions`` records per-relation candidate counts
and sampled endpoints, so fanout across expanded relations stays auditable.
Parallel edges never multiply weights — matching stays logical.

Seeds are data, not snapshots
----------------------------------------

``inferred_seeds`` only validates and canonicalizes endpoint strings. No
snapshot is built or mutated; callers feed the result to sampler seed APIs
explicitly and keep snapshot authentication at the sampler boundary.
Composition of expansion with demand specialization or stratified negation
stays out of scope per the broader composition design; each mode is invoked
explicitly and documented with its complete strategy.
