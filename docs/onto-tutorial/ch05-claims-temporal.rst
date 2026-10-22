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

   V_s(h) = \bigcup_{d \in D_s(h)} \; \bigcap_{p \in \mathrm{premises}(d)} V_p(h),
   \qquad \mathrm{holdsThroughout}(s,W,h) \iff W \subseteq V_s(h)

At a pinned system horizon ``h``, ``D_s(h)`` denotes the admissible positive
supports for statement ``s``. For this positive-rule illustration, a derivation
is valid where *all* premises overlap; alternative derivations contribute a
*union*. An assertion supplies its selected valid interval directly. This is
not the definition of every window-query policy or of negation/equality.
For example, supports covering ``[1,2)`` and ``[2,3)`` together cover ``[1,3)``,
even though neither support alone does. ``[1,2)`` and ``[3,4)`` leave a real gap;
replacing them by their min/max envelope would invent truth at time 2.5.
Truth coverage may coalesce while evidence history keeps both supports.

Temporal closure evaluates change segments, not samples at one instant
(``reasoning/temporal_closure.py``, ``intervals.py``; slice RE-E4, #160).
Confidence-calculus policy contracts are separately gated; they do not give
the default evaluator probabilistic semantics (#186, RE-25).
Domain/range *entail* memberships; validation
(``reasoning/validation.py``) *reports* conflicts without explosion.

Theory and further reading
---------------------------

- `Temporal database <https://en.wikipedia.org/wiki/Temporal_database>`_
  distinguishes valid time (when the world was so) from transaction/system
  time (when the database recorded it). GestaltDB keeps exactly this split:
  valid intervals on versions and a selected system horizon on reads.
  ``verify_closure`` checks supported range results against independently
  recomputed change segments; it is not a general database-recovery verifier.
- `Allen's interval algebra
  <https://en.wikipedia.org/wiki/Allen%27s_interval_algebra>`_ catalogues the
  relations between time intervals (before, meets, overlaps, …). The tutorial
  uses concrete half-open ``[start, end)`` intervals: touching intervals
  (``[1,2)``, ``[2,3)``) share no instant. Allen's qualitative relations are
  useful background, not a claim that this module implements that algebra.
- `Open-world assumption
  <https://en.wikipedia.org/wiki/Open-world_assumption>`_ vs `closed-world
  assumption <https://en.wikipedia.org/wiki/Closed-world_assumption>`_:
  claims default to open-world (absent evidence means ``unknown``, not false);
  only explicitly ``closed`` scopes license negation-as-absence (ch03).
- `Kripke semantics <https://en.wikipedia.org/wiki/Kripke_semantics>`_
  (possible worlds linked by accessibility relations) is the model behind the
  modal/belief layer: each agent's frame is a set of worlds, and ``entails``
  checks the formula in the worlds reachable under the depth/state bounds.
