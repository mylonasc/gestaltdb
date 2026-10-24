The cost of reasoning: what becomes expensive, and why
======================================================

Why a fast rule is not necessarily a fast application
-----------------------------------------------------

Our worked example adds two facts almost immediately. It is easy to conclude
that scaling merely requires a faster rule loop. In an actual application,
however, the input must be read, decoded, authenticated, mapped, and sometimes
converted into another representation before the loop starts. An explanation
may then need to be built, and publication may require another consistency
check. Optimizing the middle cannot remove the surrounding costs.

A useful way to reason about the whole pipeline is:

.. math::

   T_{\mathrm{total}} = T_{\mathrm{extract}} + T_{\mathrm{compile}}
       + T_{\mathrm{convert}} + T_{\mathrm{fixpoint}}
       + T_{\mathrm{evidence}} + T_{\mathrm{publish}}

These are conceptual terms, not a promise that every executor exposes a separate
timer for each. The equation helps ask the right question. If authentication
dominates a tiny query, parallelizing a join will barely change response time.
If many queries reuse one ontology, amortizing compilation may matter more than
a small per-round improvement. Measure the workload you actually repeat.

Joins: avoid combinations before making them faster
----------------------------------------------------

The work-location rule joins appointments to locations on organization ID.
A naive nested loop compares every appointment with every location. If the
organization is already bound to Acme, a lookup into a matching index bucket
can eliminate most comparisons. This is why binding propagation and index
selection are foundational reasoning capabilities, not cosmetic optimizations.

The default indexed Python evaluator exploits such bindings. Prepared plans
can reuse scheduling and inspection work; a columnar executor can use native
join kernels over compact columns. Neither approach wins on every workload.
Converting a handful of facts into a heavy runtime may cost more than the joins
it saves. Highly skewed keys can create large intermediate relations even when
the final answer is small. Reordering a join changes work, not the mathematical
set of answers, and explanations must preserve the meaning of premise positions.

Compact batches also have a boundary. A binary row of two uint64 IDs uses
16 payload bytes, but the dictionary, reverse maps, temporary Python objects,
join state, and support indexes still occupy memory. Arrow imports copy and
canonicalize; little-endian exports can share immutable column payloads, while
wrappers allocate and downstream consumers may copy. "Columnar" and "zero-copy"
are not synonyms. See the library's
`batch reference <https://github.com/mylonasc/gestaltdb/blob/main/docs/reasoning-batches.rst>`_ for ownership details.

Some costs are inherent in the requested answer
------------------------------------------------

Consider a chain of ``n`` organizations linked by ``partOf``. Computing every
reachable pair yields ``n(n-1)/2`` distinct pairs. The input is linear in ``n``;
the full closure is quadratic. No improvement in index lookup can store that
explicit output in constant memory. A selective query, a compressed structure,
or a different interface may avoid producing it, but that is a change in the
requested artifact, not a faster way to materialize the same rows for free.

Proofs and updates add further output. An answer may have several supports;
keeping enough of them for sound deletion is more work than retaining one
witness. Temporal results may split one true interval into many evidence
segments. A database with few entities can consequently still have expensive
maintenance or explanation workloads.

Full recomputation versus maintenance
--------------------------------------

If one isolated appointment changes, rebuilding everything seems wasteful.
Dependency indexes can identify potentially affected consequences, and a
support-aware algorithm can preserve unaffected answers. This is the motivation
for incremental maintenance. It is not a universal shortcut: a change near the
root of a dense hierarchy can affect most of the closure, and tracking supports
has its own cost. The crossover between local maintenance and rebuilding should
be measured rather than guessed.

Correctness remains non-negotiable. Counts alone can preserve a self-supporting
cycle incorrectly after the original assertion disappears. A supported deletion
algorithm must remove such unsupported consequences or explicitly fall back to
rematerialization. A faster partial or stale result must not be labeled a
complete current closure.

Reading measurements without overgeneralizing
-----------------------------------------------

GestaltDB's documented comparison of indexed Python and its experimental
Polars closure path illustrates the trade-off. In one historical Python 3.13
comparison with three fresh workers per cell, dense N=128 pipeline medians were
534.73ms and 46.44ms respectively. Sparse recursive N=1024 also favored Polars,
but hierarchy, duplicate-evidence, and small N=16 cases favored Python. Peak
worker RSS was approximately 54 MiB for Python versus 127 MiB for Polars.
These are workload observations, not a promise about your machine or about
proof/maintenance modes that the prototype does not provide.

The timings include compilation, conversion, fixpoint, and decoding but exclude
package imports and fixture construction. That definition matters as much as
the numbers. The complete tables, scope, and reproduction commands are in
the library's `Polars guide <https://github.com/mylonasc/gestaltdb/blob/main/docs/reasoning-polars.rst>`_;
there is no reason to memorize the figures here.

When evaluating your own pipeline, distinguish cold-start latency from reused
execution, final payload from peak process RSS, and closure-only results from
evidence-bearing ones. Cooperative cancellation stops at supported checkpoints;
it does not interrupt an already-running native kernel or provide a hard
process-memory guarantee. Such requirements need an appropriate execution
boundary, not merely another row counter.

Check your understanding
-------------------------

**Question.** A native join kernel is ten times faster in isolation. Must the
application also become ten times faster? Can a chain's complete reachability
closure use linear explicit row storage?

**Answer.** No to both. Extraction, conversion, evidence, and publication remain,
so the end-to-end improvement depends on their share of the cost. The chain's
quadratic number of distinct output pairs requires corresponding explicit row
space; avoiding those rows means choosing another answer representation or a
more selective question.
