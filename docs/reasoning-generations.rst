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
old-or-new generations, never a stale closure.

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
