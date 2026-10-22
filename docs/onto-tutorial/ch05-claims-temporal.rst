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

Theory and further reading
---------------------------

- `Temporal database <https://en.wikipedia.org/wiki/Temporal_database>`_
  distinguishes valid time (when the world was so) from transaction/system
  time (when the database recorded it). GestaltDB keeps exactly this split:
  ``validFrom``/``validTo`` on versions, ``systemFrom`` on commits — which is
  why ``verify_closure`` can replay history instead of trusting one snapshot.
- `Allen's interval algebra
  <https://en.wikipedia.org/wiki/Allen%27s_interval_algebra>`_ catalogues the
  relations between time intervals (before, meets, overlaps, …). The tutorial
  uses half-open ``[start, end)`` intervals rather than Allen's closed ones:
  touching intervals (``[1,2)``, ``[2,3)``) are then disjoint by construction,
  so coalescing never needs an epsilon.
- `Open-world assumption
  <https://en.wikipedia.org/wiki/Open-world_assumption>`_ vs `closed-world
  assumption <https://en.wikipedia.org/wiki/Closed-world_assumption>`_:
  claims default to open-world (absent evidence means ``unknown``, not false);
  only explicitly ``closed`` scopes license negation-as-absence (ch03).
- `Kripke semantics <https://en.wikipedia.org/wiki/Kripke_semantics>`_
  (possible worlds linked by accessibility relations) is the model behind the
  modal/belief layer: each agent's frame is a set of worlds, and ``entails``
  checks the formula in the worlds reachable under the depth/state bounds.
