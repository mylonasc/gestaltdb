:orphan:

Temporal delta maintenance and interval publication
============================================================

``update_range`` propagates changed validity ranges without rebuilding the
world: segments whose active base set is unchanged reuse their derivations
verbatim, and only genuinely new active combinations re-evaluate. Removals
are support-aware — a derived fact disappears only when its last supporting
segment goes, and every removal traces to changed premises. The
``incremental=False`` path (or any stale generation) falls back to a full
``evaluate_range`` instead of degrading, with point/range parity holding in
both modes.

.. code-block:: python

   from gestaltdb.reasoning import evaluate_range, update_range
   from gestaltdb.reasoning.model import ReasoningLimits

   closure = evaluate_range(program, base, limits=ReasoningLimits())
   out = update_range(program, closure, changed_base, limits=ReasoningLimits())
   assert out.reused_segments + out.recomputed_segments == len(out.closure.segments)
   fresh = evaluate_range(program, changed_base, limits=ReasoningLimits())
   assert {f.fact for f in out.closure.derivations} == {f.fact for f in fresh.derivations}

Managed interval generations
----------------------------------------

``stage_range``/``publish_staged_range`` extend the staged
compare-and-publish pattern to caller-derived interval claims: each
validity interval is its own reserved namespace, generations bind exact
source/rule identities plus the caller's evidence digest, and gaps between
generations are never filled nor enveloped. Stale namespaces raise
``GenerationStaleError`` for explicit re-staging; interruptions surface as
unreclaimed temporal artifacts for consent-gated recovery. Single-quantum
``reconcile_point`` behavior is preserved unchanged.

Measured cost
----------------------------------------

A 30-edge sliding-window range (39 segments): one fact's validity shrink
reuses 38 segments and recomputes 1, completing in ~22ms against ~235ms
for a full rebuild — incremental work scales with changed segments, not
total closure. Complete per-interval support discovery remains the
dominant cost inside recomputed segments; measure before assuming an
advantage on dense global changes.
