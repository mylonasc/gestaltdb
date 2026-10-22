Storage grounding: from facts to pinned reads
================================================

Core and compiler never touch storage. Grounding is explicit:

* ``reasoning/extraction.py`` — bounded ``ExtractionRequest`` / ``FactBatch``
  contracts with exact version evidence; ``reasoning/adapters/batched.py``
  borrows caller views; LevelDB snapshot requests fail closed.
* ``reasoning/adapters/native.py`` — audited PyRex Arrow scans, version-gated
  (``pyrex-rocksdb>=0.5.0``); decoding stays caller-owned (#194).
* ``reasoning/adapters/graph.py`` — explicit current-graph projection and
  per-relation fanout (:doc:`../ontology-graph`).

.. math::

   \text{extract}(G@h, \varphi) \to \langle \text{Facts}, h \rangle
   \qquad h = \text{pinned horizon}

Point-only publication (``reasoning/adapters/publication.py``,
:doc:`../ontology-publication`) stages generations with compare-and-publish;
nontransactional orphan recovery is caller-managed, never silently repaired.
Maintenance (``reasoning/maintenance.py``, ``catalog.py``) adds staged
generations, forward/reverse dependency indexes, insertion propagation and
DRed deletion — live-reasoning slice RE-E3 (#159); reuse and session-resume
gaps are #249–#253.
