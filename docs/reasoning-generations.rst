:orphan:

Recoverable staged generations and compare-and-publish
============================================================

Point publication now splits into ``stage_point`` (extraction + inference,
no writer guard) and ``publish_staged`` (guard held only for orphan audit,
target comparison and one logical commit). The stage binds database,
namespace, ontology revision, policy, mapping, evidence and valid point in
its generation digest plus a ``base_fingerprint`` of the visible target
state. Publication compares the fingerprint under the guard and raises
``GenerationStaleError`` on any movement — readers only ever observe
old-or-new generations.

Stages retain authenticated source read-view provenance. The default
``publish_staged(..., freshness="latest")`` also requires the current commit
horizon to match that source horizon. This first implementation conservatively
rejects unrelated intervening commits too; re-stage explicitly to retry. It
does not silently publish conclusions whose source assertions were retracted.

Historical publication requires explicit ``freshness="historical"``. It verifies
the original pinned source prefix rather than pretending it is current. Both
modes retain target compare-and-publish checks and record the source horizon,
provenance token and freshness policy in published claim provenance. Legacy
manually constructed stages without source provenance must be re-staged.

New point stages also carry ``generation_binding``: the shared authenticated
``ClaimGenerationBinding`` with database/horizon, input context, compiled
program and catalog digests, binary-entity semantics and external-string
dictionary identity. Stage construction rejects mismatched world, policy,
mapping, revision, provenance or representation. Publication revalidates the
binding under the same writer guard; the binding alone is not a publication
permit or proof of derivation. New claim writes and their commit metadata
record ``reasoning_generation``, ``program_digest`` and ``catalog_digest``.
The existing publication generation ID and target fingerprint remain unchanged.
Legacy stages with source provenance but no shared binding retain the existing
freshness checks; unchanged claims are not rewritten merely to add metadata.
This migration covers binary point publication only, not typed sessions,
current-graph admission, temporal ranges or cache reuse.

Callers that retain an expected ``GenerationIdentity`` may pass it as
``publish_staged(..., expected_generation=identity)``. All source, context,
program, catalog, profile and dictionary fields must match the stage's shared
binding before publication proceeds. This opt-in check rejects legacy stages
without that binding; re-stage explicitly rather than guessing their identity.
A matching pin never bypasses freshness or target compare-and-publish checks,
and historical mode does not waive compatibility. Omitting the pin preserves
existing behavior. Identity pins describe source evaluation, not the output
namespace, desired facts or a proof that the computation is sound.

.. code-block:: python

   staged = stage_point(graph, compiled, valid_time=t, source_world="base",
                        target_world="derived", publication_key="k",
                        selection=selection, mapping=mapping, limits=limits)
   try:
       result = publish_staged(graph, staged)
   except GenerationStaleError:
       staged = stage_point(graph, compiled, valid_time=t, source_world="base",
                            target_world="derived", publication_key="k",
                            selection=selection, mapping=mapping, limits=limits)
       result = publish_staged(graph, staged)

Retries are explicit: the caller re-stages and republishes. Concurrent
writers serialize on the writer guard; the loser observes a stale base and
retries without overwriting. Durability follows the existing temporal commit
path; ``commit_versions`` fsync behavior is unchanged. Reader generation
retention follows pinned read views: open readers keep their commit prefix
while the new generation becomes visible to fresh readers.

Recovery is consent-gated: ``audit_publication_orphans`` only lists,
``reclaim_publication_orphans(..., consent=True)`` deletes validated
unpublished artifacts through an explicit horizon. Without ``consent=True``
it fails closed without touching storage. Fault injection (simulated
incomplete commits) must be reclaimed explicitly; there is no silent index
repair.
