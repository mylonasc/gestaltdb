Gaps and pain points
======================

What is explicitly missing or deferred (fail-closed, never silent):

* Profiles: disjunctive heads, existentials/EL (bounded prototype #185),
  full OWL RL/DL — construct matrix + approval gates in #184; no badge claims.
* Negation (#171, #125): scoped stratified only, recomputation fallback;
  incremental invalidation stays in RE-16.
* Equality (#172): snapshot-local canonical representatives; literals never
  merge; no unique-name assumption; temporal representatives rebuild per
  segment.
* Aggregates: nonrecursive/stratified only; recursive/lattice deferred.
* Extraction: decoded fact extraction over native scans (#194); demand
  pushdown into pinned reads (#249); full-body-scan avoidance (#252);
  dependency-index reuse (#253); resumable sessions (#251); fact
  multiplicities (#250); generation identity unification (#248); inferred
  generation consumption (#256); pipeline glue (#255).
* Scale: join-shape profiler, differential updates, spill/worker ownership —
  conditional on measured need (RE-26, #187); ranked/minimal proofs and
  checkpoints in RE-27 (#188).
* Tracker: techdebt umbrella #200; roadmap phases RE-E1..E5 (#157–#161);
  neuro-symbolic (#128), weights (#124), explanations (#126) coordinate but
  stay separate.

Rule of thumb for this tutorial: if a combination is not in the supported
matrix (``reasoning/profiles.py`` ``MATRIX``), the code must reject it — and
this tutorial must say so.
