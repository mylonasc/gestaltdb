Claims, time, and belief
==========================

Why a triple is sometimes not enough
-------------------------------------

So far, ``worksFor(alice, acme)`` has been an unqualified assertion. A research
directory eventually needs to ask *who supplied it*, *when the appointment was
valid*, and *what was known when a report was produced*. Attaching a date to a
node's display properties does not automatically answer those questions.

Consider an appointment valid from January through March. The database first
records it in February, then receives a correction in April saying it actually
ended in February. A report generated in March and a corrected report generated
in May may answer the same question about March differently. **Valid time**
describes the modeled world; **system time** describes which recorded history
the application is allowed to consult. Their separation is called bitemporality.
It supports historical audit and avoids presenting later corrections as if they
had been known earlier.

GestaltDB keeps mutable graph records, immutable temporal versions, and sourced
claims as separate layers. ``put_node_version`` and ``commit_versions`` append
history; as-of reads select it. ``assert_claim``, ``correct_claim``, and
``retract_claim`` record sourced propositions through that history. These are
different workflows from changing a current node's properties.

Missing evidence is not a negative assertion
---------------------------------------------

Suppose one source affirms Alice's appointment and another explicitly disputes
it. Removing one physical edge would conceal the disagreement. A claim layer
can retain both pieces of evidence. ``claim_status`` distinguishes
``supported``, ``refuted``, ``both``, and ``unknown``. These are evidence states,
not four confidence scores: ``unknown`` means neither positive nor negative
evidence was found in the selected context, while ``both`` records a conflict.

This explains why absence must be treated carefully. If the directory lacks
Bob's appointment, it has not established that Bob has no appointment. To
reason from absence, an application must declare an appropriate closed-world
scope, such as an authoritative and complete roster for a particular horizon.
The generic engine's scoped stratified negation is a separate facility from
the positive binary programs used earlier. It cannot be enabled by simply
interpreting every missing fact as false.

Combining time with derivations
--------------------------------

If a conclusion needs two premises, it holds only where both premises are valid.
If either of two independent derivations is enough, their valid intervals offer
alternative support. This motivates intersection *within* a derivation and
union *across* derivations. Using half-open ``[start, end)`` intervals makes
boundary behavior explicit: the ending instant belongs to the next interval,
not to both.

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

Temporal closure therefore evaluates supported change segments, not just a
sample at one instant. See :doc:`../reasoning-temporal` for the supported
contracts. It is a stronger task than attaching a timestamp to a point answer.

Why deleting an assertion can be difficult
-------------------------------------------

Suppose Alice's Person membership follows both from the appointment and from
an independently sourced Employee assertion. Deleting the appointment must
not remove the still-supported Person membership. Conversely, a cycle of rules
must not keep an answer alive forever after its final real support disappears.
This is the problem of **truth maintenance**: preserving exactly the conclusions
still justified after changes, not counting an arbitrary number of proof paths.

GestaltDB's positive binary maintenance API tracks bounded supports and uses
DRed-style deletion—overdelete potentially affected consequences, then
rederive those still justified. Full rematerialization is the explicit fallback
for unsupported changes or combinations. A chosen explanation witness alone
is not sufficient state for safe deletion. The detailed supported boundaries
are in :doc:`../reasoning-maintenance`.

Belief and confidence answer different questions
-------------------------------------------------

Finally, "Alice works for Acme" and "agent A believes Alice works for Acme"
are different propositions. Modal evaluation uses worlds linked by declared
accessibility relations; ``entails`` evaluates bounded formulas in that frame.
An accessibility link is not a physical social-network edge unless the
application explicitly models it that way.

Neither a source name, multiple proofs, nor a belief operator automatically
produces a probability. Confidence calculi need separately chosen policies;
the default rule evaluator does not assign probabilistic truth. Similarly,
domain/range rules derive memberships, while validation reports selected
conflicts. These services should not silently replace one another.

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

Check your understanding
-------------------------

**Question.** Bob has no appointment claim in our selected snapshot. Is his
appointment refuted? If we later remove one of two independent supports for
Alice's Person membership, must that membership disappear?

**Answer.** No to both. Missing evidence gives ``unknown`` under the open-world
reading; an explicit refutation or an authorized absence-based policy is
different. Alice remains supported while an independent valid derivation
survives in the selected context.

Losing the last derivation of Person membership would mean that this model no
longer supplies that conclusion. It would not assert that Alice has stopped
being a person. That difference is exactly why an open-world interpretation
and a careful choice of domain axioms matter.
