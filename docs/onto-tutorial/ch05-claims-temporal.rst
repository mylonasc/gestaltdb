Claims, time, and belief
==========================

Mutable property records (``graphdb.py``), temporal versions
(``versioning.py`` / ``temporal.py``) and epistemic claims
(``epistemic.py``) are three separate layers.

* Bitemporal versions: ``put_node_version`` / ``commit_versions`` append
  immutable history; ``get_node_as_of`` / ``iter_edges_as_of`` read it.
  Intervals are half-open ``[start, end)`` microsecond UTC.
* Claims: ``assert_claim`` / ``correct_claim`` / ``retract_claim`` with
  deterministic statement IDs; ``claim_status`` gives open-world
  ``supported`` / ``refuted`` / ``both`` / ``unknown``.
* Modal: ``assert_world_accessibility`` + ``entails`` evaluates bounded
  nested formulas under one read view with depth/state bounds.

.. math::

   \text{holds}(s, [t_1, t_2)) \iff \exists\, v : \text{valid}(v) \supseteq
   [t_1, t_2) \;\land\; \text{visible}(v, h)

Temporal closure evaluates change segments, not samples at one instant
(``reasoning/temporal_closure.py``, ``intervals.py``; slice RE-E4, #160).
Confidence/fuzzy combination is a separately-selected calculus, never silent
multiplication (#186, RE-25). Domain/range *entail* memberships; validation
(``reasoning/validation.py``) *reports* conflicts without explosion.
