Choosing a supported reasoning workflow
========================================

Start with the question, not with a standards label
----------------------------------------------------

We began with a small question about Alice and built the pieces needed to
answer it: a graph, an explicit meaning for its vocabulary, an engine for
implications, a consistent source view, and a way to consume the result. It is
tempting to call the combined system "an OWL reasoner" and assume every later
semantic task is covered. That would erase the most important design boundary:
different questions require different languages and guarantees.

If the application needs class inheritance, domains/ranges, inverses,
transitivity, or selected binary property chains over named entities, the
ontology compiler and positive binary evaluator are a natural starting point.
They provide a deliberately bounded fragment, not complete RDFS or OWL. A
standard's name in a diagram is not a conformance certificate.

If the application needs typed literal comparisons, n-ary relations, or supported
aggregates, inspect the separate generic typed API. If it needs absence-based
rules, determine which predicates and contexts can legitimately be treated as
closed, then check the scoped stratified-negation contract. Neither feature
appears automatically in the binary ontology engine merely because another
module supports it.

Some problems need a different engine family
---------------------------------------------

Suppose the question changes from "What follows from these appointments?" to
"Which of several possible appointment assignments satisfy all our constraints?"
That is a search over possible solutions, not simply a positive closure. An
answer-set solver such as Clingo addresses such choices under its own semantics;
it is not a drop-in replacement with identical answers and resource behavior.

Likewise, arbitrary anonymous class restrictions, unrestricted existential
generation, cardinalities, and complete OWL DL satisfiability do not follow
from the selected named-entity fragment. GestaltDB has separately gated
experimental capabilities, including bounded EL work, but their existence
does not expand the compiler used in the introductory examples. Start from
the required constructs and inspect their exact supported semantics. See
:doc:`../reasoning-profiles` and :doc:`../reasoning-el` for those boundaries.

Validation is another distinct need. "Every record must contain a field" is
not the same statement as an ontology domain implication. Selected conflict
reports are useful, but should not be sold as a complete shapes validator or a
general proof that an ontology is satisfiable. Similarly, deterministic proofs
do not constitute a probability model. Pick an explicit validation or confidence
contract rather than inferring one from a familiar class name.

An available feature may not compose with every other one
---------------------------------------------------------

Combining independently useful features is often the hardest engineering work.
A snapshot-local equality mapping, for example, must not be reused after the
snapshot changes as though identities had been physically merged forever.
Temporal equality may need different representatives in different segments.
A literal value must not silently become an entity ID.

The positive binary maintenance API similarly does not cover every generic
rule feature. Changed programs, closed scopes, negation, aggregates, and equality
splits require the documented explicit recomputation path rather than an
unsupported incremental update. Closure checkpoints and bounded one-step
ranked witnesses are not the same as durable resumable database-delta sessions
or globally minimal recursive proof trees.

The same caution applies to execution. Compact batches are not a native
reasoner; a worker/spill policy object is not an out-of-core runtime; a
closure-only Polars result is not a proof-bearing publication generation.
For each workflow, check the whole chain from input identity through semantics,
limits, evidence, and consumption. Unsupported requested guarantees should
produce an explicit failure, not a plausible-looking weaker result.

Integration pain points to plan for
-------------------------------------

The tutorial's explicit boundaries make behavior understandable, but they also
leave application work. Choosing which extracted facts are relevant, binding a
selective goal to a pinned database read, persisting a long-running incremental
session, and exposing authenticated inferred results to query/sampling consumers
are separate integration problems. Do not budget for them as if an in-memory
``evaluate`` call supplied an entire managed service.

Performance evidence has boundaries too. Native decoded evidence helpers exist,
but small fixtures cannot establish a large-scale throughput advantage. A new
optimization may improve lookup work while leaving full-state authentication,
output size, or alternative-support storage dominant. Chapter 7 explains why
the end-to-end measurement matters.

These limitations do not make reasoning useless. They tell us where it is
useful: explicit, testable consequences over a declared fragment, with visible
boundaries around storage, evidence, time, and consumption. Start with that
complete small workflow; add richer semantics or acceleration only when a
concrete question requires it.

What you should now be able to explain
----------------------------------------

Return to the directory question. Alice's appointment is stored data; the
Employee-to-Person relationship is a modeling axiom; the rule engine computes
membership, with Researcher requiring an extra job-description premise;
a pinned adapter binds the input to a source state; and a logical
query reads the consequence without silently mutating the stored labels.
Changing one of those steps changes either the question, the assumptions, or
the execution guarantee. Being able to distinguish them is more valuable than
remembering a particular import path.

Check your understanding
-------------------------

**Question.** A proposed feature needs typed aggregates, temporal equality,
incremental retractions, and proof-bearing publication. Is it enough to find
one module advertising each feature?

**Answer.** No. Check whether the combination is supported by the same input,
evaluation, maintenance, and publication contracts. If not, choose a documented
complete recomputation strategy, split the workflow explicitly, or use a
different engine whose semantics and guarantees meet the requirement. Do not
compose feature names into a guarantee the system never made.
