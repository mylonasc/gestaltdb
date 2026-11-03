Build negative training examples
=================================

A :term:`negative sample` is a training candidate under a rejection policy, not
evidence that a proposition is false. Knowledge-graph negatives replace an
endpoint of a positive triple while rejecting known positives. They differ from
walk-context negatives used by an embedding objective and from explicit negative
epistemic claims. Start with :doc:`sampling-introduction` for compact IDs.

Reject known positives
------------------------

This standalone array example contains ``drug-a binds protein-x`` and
``drug-b binds protein-y``. It asks for one tail replacement per positive and
tries other positive tails in the batch first. Both crossed pairs are absent
from the snapshot and satisfy this fixture's policy.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           f"{root}/snapshot",
           external_node_ids=["drug-a", "drug-b", "protein-x", "protein-y"],
           external_edge_ids=["ax", "by"], external_relation_ids=["binds"],
           src_int=[0, 1], dst_int=[2, 3], rel_int=[0, 0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       negatives, diagnostics = engine.sample_hard_negatives(
           snapshot.positive_triples,
           config=HardNegativeConfig(
               negatives_per_positive=1, in_batch=True, head_probability=0.0,
               exhaustion_policy="raise",
           ),
           return_diagnostics=True,
       )
       external = {snapshot.global_triple_to_external(triple)
                   for triple in negatives[:, 0, :]}
       assert external == {
           ("drug-a", "binds", "protein-y"), ("drug-b", "binds", "protein-x"),
       }
       assert diagnostics["accepted_in_batch"] == 2
       known = set(map(tuple, snapshot.positive_triples))
       assert all(tuple(triple) not in known for triple in negatives[:, 0, :])

The output shape is ``(positive_count, negatives_per_positive, 3)`` with triples
``(head, relation, tail)`` in global compact snapshot IDs. Subgraph batches instead
use local endpoint rows; map through ``node_ids_global`` before interpreting them.
Type compatibility is separate from a node label: snapshots use their recorded
semantic type IDs (graph builds default to the ``kind`` property).

Choose the rejection and exhaustion policy
-------------------------------------------

- ``in_batch=True`` tries same-relation peer endpoints first. Normal type,
  availability, duplicate and positive-rejection checks still apply. Context and
  random fallback run when the peer pool is exhausted.
- ``candidate_distribution="degree"`` weights only the random fallback by
  incident degree raised to ``degree_exponent``. It does not reweight peer or
  context proposals; a graph without incident edges falls back to uniform.
- ``exhaustion_policy="raise"`` fails when the requested accepted pool cannot be
  filled. ``"repeat"`` reuses an accepted candidate deterministically; it does
  not permit known positives or invent an acceptable candidate where none exists.
- Known-positive rejection is relative to the selected snapshot and policy,
  not a complete-world truth test or automatic train/validation split.

For temporal snapshots, the default ``temporal_positive_policy="any_time"``
rejects positives anywhere in the known history. ``"at_positive_time"`` checks
each example instant; ``"window"`` checks an explicit window. Direct temporal
calls require aligned ``positive_times_us`` or a temporal context unless future
candidates are explicitly allowed. A trailing candidate window constrains
availability separately; ``allow_future_candidates=True`` is an explicit opt-in,
not implied by choosing point-positive rejection. See
:ref:`sampling-negative-reference` for exact window, diagnostic and alignment
contracts, and :doc:`sampling-temporal` for the positive sampling timeline.

For Skip-gram/context negatives, use ``ContextNegativeSampler`` in
:doc:`random-walks`. It serves a different objective and does not promise
knowledge-graph positive rejection.
