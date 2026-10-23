:orphan:

Snapshot-local equality and functional identity
============================================================

``gestaltdb.reasoning.equality.canonicalize`` rebuilds union-find state from
scratch on every call over one explicit fact set: sameAs/differentFrom links
plus conflicts generated from functional properties. Representatives are the
lexicographically smallest members, aliases are preserved, and every merge
carries its exact link evidence. Nothing is merged physically, no unique-name
assumption is made, and literals never merge.

.. code-block:: python

   from gestaltdb.reasoning.equality import (
       DifferentFrom, EqualityScope, FunctionalProperty, SameAs, canonicalize,
   )
   from gestaltdb.reasoning.typed import TypedFact

   mapping = canonicalize(
       [TypedFact("mbox", ["n", "a@x"]), TypedFact("mbox", ["n", "b@x"])],
       [FunctionalProperty("mbox"), DifferentFrom("a@x", "b@x")],
       scope=EqualityScope("snapshot-1"),
   )
   assert mapping.representative("b@x") == "a@x"
   assert mapping.members("a@x") == ("a@x", "b@x")
   assert mapping.justification("a@x") == (SameAs("a@x", "b@x"),)
   assert len(mapping.conflicts) == 1  # the violated inequality, with evidence

Canonicalization, not merging
----------------------------------------

The input facts are never rewritten in place. ``rewrite`` returns a new tuple
with entity values replaced by representatives (literals untouched), for
callers that explicitly opt into canonical reading. Member aliases stay
available through ``members``; the mapping digest binds scope, classes,
justifications and conflicts, so equal inputs under equal scopes agree.

Conflicts are first-class results, not failures: a ``differentFrom`` pair
landing in one class yields an ``InequalityConflict`` with the merge path and
the violated link. Functional conflicts likewise report the two values and
state that equality is *not* inferred — generation beyond explicit links is
out of scope, and key/cardinality checks beyond single-valued functionality
belong to RE-23.

Removal, scope and time
----------------------------------------

Because no state persists between calls, removing support can only split
classes on the next rebuild; insertion-only union-find reuse is explicitly
not implemented. Each mapping is valid for exactly the evaluated snapshot:
callers pass an opaque ``EqualityScope`` (a pinned-context identity) and must
rebuild after any change; temporal scoping of representatives is revisited in
RE-20, which may rebuild at change points instead of sharing representatives
globally. Recursive equality interaction with other strata is evaluated
against the same input horizon, never against a cached older mapping.

Measured cost
----------------------------------------

Canonicalizing 100,000 entities chained by 99,999 links completes in about
0.6s single-threaded with bounded memory (parent/size/evidence tables sized by
distinct entities); see the roadmap register for the frozen reference. The
bound ``max_entities`` fails closed instead of degrading, and cooperative
``cancel`` is honored between union batches.
