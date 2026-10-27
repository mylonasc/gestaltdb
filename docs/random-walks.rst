Random-walk embedding pipelines
==============================

GestaltDB generates walk corpora and framework-neutral training inputs; it does
not train embedding models. The RWS-01–RWS-08 roadmap is tracked in GitHub #416.
All node, edge, relation and type IDs below are compact IDs from the exact same
sampler snapshot. Do not mix IDs from different snapshots.

RWS-01: streaming corpora
------------------------

``engine.iter_random_walks`` streams ordinary ``RandomWalkBatch`` objects over
all snapshot nodes or explicit seeds. See :doc:`typed-sampling` for memory,
restart, seed-round and reproducibility contracts. Consume batches rather than
collecting the full iterator. Existing walk methods and defaults are unchanged.

RWS-02: Skip-gram context pairs
------------------------------

``iter_context_pairs`` consumes a walk batch or an iterable of batches. It yields
``ContextPairBatch`` objects containing aligned ``center_nodes``,
``context_nodes``, ``distances`` and ``weights`` arrays. ``to_numpy()`` returns
these arrays without copying. No training-framework dependency is needed.

* ``window_size`` is a positive context radius on either side of each center.
* ``dynamic_window=True`` draws each center's radius uniformly in [1, window size].
* ``distance_weighting="inverse"`` emits 1/distance weights; the default is one.
* Restart jumps split sequences by default. ``cross_restarts=True`` explicitly
  allows windows across teleports. Separate walk rows are never joined; padding
  beyond ``lengths`` is ignored.
* Optional ``frequencies`` are non-negative finite counts/frequencies indexed by
  compact node ID. With ``subsample_threshold=t > 0``, normalized frequency f
  gives retention min(1, sqrt(t/f) + t/f); zero-frequency nodes are retained.
  Subsampling happens before windows, so distances refer to retained positions.
  Frequencies must come from the caller's intended corpus, not an implicit scan.
* ``batch_size`` bounds emitted pairs. Memory also includes one walk row, the
  upstream batch, and an optional copied frequency vector. Argument validation
  is eager; walk array validation is lazy. A fixed seed and identical input walks
  reproduce pairs regardless of output batch size.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, iter_context_pairs

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           root + "/snapshot", external_node_ids=["a", "b", "c"],
           external_edge_ids=["ab", "bc"], external_relation_ids=["R"],
           src_int=[0, 1], dst_int=[1, 2], rel_int=[0, 0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       walks = engine.iter_random_walks([0], walk_length=2, batch_size=1)
       pairs = list(iter_context_pairs(walks, window_size=1, batch_size=3))
       assert sum(len(batch.weights) for batch in pairs) == 4
       assert pairs[0].center_nodes.tolist() == [0, 1, 1]

RWS-03: embedding-objective negatives
------------------------------------

``ContextNegativeSampler(frequencies, exponent=0.75, seed=...)`` uses explicit
corpus counts/frequencies. ``ContextNegativeSampler.from_snapshot(snapshot)``
instead uses incident degree (self-loops counted twice). Its ``sample``
method accepts compact centers and optional aligned ``positive_contexts`` and
returns an integer array shaped (centers, negatives per positive). The caller
may exclude centers, aligned positive contexts, and all known observed contexts
via ``excluded_contexts={center: [contexts...]}``. It does not infer these sets
from the graph or equate missing edges with negative co-occurrences.

Zero-count nodes have zero probability unless all counts are zero (uniform
fallback), or exponent is zero (uniform). Non-finite/negative inputs fail.
Exclusions explicitly condition the distribution: cost is O(nodes) per row,
plus output, rather than an unbounded rejection loop. ``unique=True`` samples
without replacement. Insufficient support raises before RNG draws by default;
``exhaustion="repeat"`` cycles a sampled feasible unique set. Empty support
always raises for a nonzero request. This is separate from KG triple negatives.

.. code-block:: python

   from gestaltdb.sampling import ContextNegativeSampler

   negatives = ContextNegativeSampler([10, 5, 2, 1], seed=7)
   result = negatives.sample(
       [0], positive_contexts=[1], negatives_per_positive=2, unique=True,
   )
   assert set(result[0]) == {2, 3}

RWS-04: non-backtracking and recent-history avoidance
---------------------------------------------------

``engine.sample_policy_walks(seeds, length, policy=WalkPolicy(...))`` is an
opt-in kernel; legacy first-order/node2vec defaults are unchanged. Use
``engine.iter_random_walks(walk_type="policy", policy=...)`` to stream it.

``non_backtracking=True`` forbids returning to the previous node, including via
another parallel edge. ``avoid_recent=k`` forbids endpoints among the last k
visited nodes, including the current node: k=1 forbids self-loops, k=2 also
forbids immediate returns. History is bounded by max(k, 2), not corpus size.
``avoidance_exhaustion`` chooses ``terminate`` (default), ``restart``, or
``relax``. Relaxation drops only avoidance, never relation/temporal restrictions.
Restart jumps clear history and use ordinary -1 edge/relation markers.
True graph dead ends use the separate ``dead_end`` option. ``strategy="weighted"``
on the policy uses static edge weights; invalid all-zero candidate weights raise.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, WalkPolicy

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           root + "/snapshot", external_node_ids=["a", "b"],
           external_edge_ids=["ab", "ba"], external_relation_ids=["R"],
           src_int=[0, 1], dst_int=[1, 0], rel_int=[0, 0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       batch = engine.sample_policy_walks([0], 4, policy=WalkPolicy(non_backtracking=True))
       assert batch.lengths.tolist() == [1]
