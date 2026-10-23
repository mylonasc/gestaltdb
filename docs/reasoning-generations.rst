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
