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

Reusable closures carry ``program_digest``. Changed rule content (even with
unchanged rule IDs), or legacy closures without a digest, trigger a full rebuild
with zero reused segments. Manually constructed unbound closures can still be
verified by fresh evaluation; they are never admitted as reusable caches.

Stratified negation and aggregates (#393)
-----------------------------------------

Range evaluation covers the full evaluated fragment, not just positive
rules. Each segment runs ordinary point evaluation — stratified when the
program uses negation or closed scopes — so absence holds exactly on the
segments where the disqualifying fact is inactive, and aggregates group
exactly the rows active on their segment. Pure-positive programs keep the
legacy all-supports evidence path; everything else records first-witness
evidence (premises, scoped absence, aggregate operands) from proof traces.
``verify_closure`` point-checks with the same engine choice and additionally
verifies absence probes and aggregate operands on their segments.

.. code-block:: python

   from gestaltdb.reasoning import evaluate_range, update_range
   from gestaltdb.reasoning.model import ReasoningLimits

   closure = evaluate_range(program, base, limits=ReasoningLimits())
   out = update_range(program, closure, changed_base, limits=ReasoningLimits())
   assert out.reused_segments + out.recomputed_segments == len(out.closure.segments)
   fresh = evaluate_range(program, changed_base, limits=ReasoningLimits())
   assert {f.fact for f in out.closure.derivations} == {f.fact for f in fresh.derivations}

Proof budgets never remove conclusions
--------------------------------------

``TemporalClosure`` stores complete logical ``conclusions`` separately from
``derivations``. ``max_proof_nodes`` and ``max_proof_edges`` bound witness
storage only: exhausting them sets ``proofs_truncated``, emits
``TemporalProofTruncatedWarning`` and leaves ``at()``, ``answers()`` and
``missing_evidence()`` truthful.

.. code-block:: python

   import warnings
   from gestaltdb.reasoning import (
       ReasoningLimits, TemporalProofTruncatedWarning, evaluate_range, verify_closure,
   )

   with warnings.catch_warnings(record=True) as caught:
       warnings.simplefilter("always")
       closure = evaluate_range(program, base, limits=ReasoningLimits(max_proof_nodes=1))

   assert any(isinstance(item.message, TemporalProofTruncatedWarning) for item in caught)
   assert closure.answers(fact).intervals  # logical answer remains complete
   assert closure.missing_evidence()      # explanations are explicitly partial
   assert verify_closure(program, closure, limits=ReasoningLimits()).proofs_truncated

A tighter proof budget cannot change derived validity: compare against the same
run with default limits. When a previous closure is truncated, ``update_range``
rematerializes instead of reusing partial explanations; the new result may still
be truncated under the same budget and warns again. Limits on logical work
(``max_segments``/``max_supports``), cancellation and malformed inputs still
raise rather than returning a partial answer.

Managed interval generations
----------------------------------------

``stage_range``/``publish_staged_range`` extend the staged
compare-and-publish pattern to caller-derived interval claims: each
validity interval is its own reserved namespace, generations bind the
caller's explicit ontology revision plus source/rule identities and the
evidence digest, and the revision is published into each claim's
provenance. Stages capture authenticated read-view provenance at staging time.
The default ``publish_staged_range(..., freshness="latest")`` also
requires the current commit horizon to match that source horizon, so
commit movement between staging and publication is rejected;
explicit ``freshness="historical"`` verifies the old pinned prefix
instead. Both modes retain target compare-and-publish checks and record
the staging horizon and freshness policy in published claim provenance
and commit metadata. The provenance token is recorded in claims only.
Legacy stages without source
provenance must be re-staged. Gaps between generations are never filled
nor enveloped. Stale namespaces raise ``GenerationStaleError`` for
explicit re-staging; interruptions surface as unreclaimed temporal
artifacts for consent-gated recovery. Single-quantum
``reconcile_point`` behavior is preserved unchanged.

Unlike ``stage_point``, ``stage_range`` receives already-derived triples; it
does not extract inputs or evaluate the caller's program inside that view.
The captured horizon therefore authenticates the staging prefix, not the
computation that produced those triples. A stale computation performed before
staging is not detected by the latest check. The caller remains responsible
for binding its range evaluation and evidence digest to the intended source
prefix and program. General authenticated range-result admission is not
provided by this API; changing to historical mode does not verify a derivation
either. Published metadata must not be treated as an independent closure proof.

For authenticated finite-range source extraction, evaluation and re-admission,
see :doc:`reasoning-range-admission`. That separate API does not change this
raw caller-derived staging/publication contract or grant batch atomicity.
For an explicit authenticated computation-to-publication consumer with one
logical interval-batch commit, see :doc:`reasoning-bound-range-publication`.

Measured cost
----------------------------------------

A 30-edge sliding-window range (39 segments): one fact's validity shrink
reuses 38 segments and recomputes 1, completing in ~22ms against ~235ms
for a full rebuild — incremental work scales with changed segments, not
total closure. Complete per-interval support discovery remains the
dominant cost inside recomputed segments; measure before assuming an
advantage on dense global changes.
