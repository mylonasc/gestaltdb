Random-walk embedding pipelines
======================================================================

GestaltDB generates walk corpora and framework-neutral training inputs; it does
not train embedding models. The RWS-01–RWS-08 roadmap is tracked in GitHub #416.
All node, edge, relation and type IDs below are compact IDs from the exact same
sampler snapshot. Do not mix IDs from different snapshots.

RWS-01: streaming corpora
----------------------------------------------------------------------

``engine.iter_random_walks`` streams ordinary ``RandomWalkBatch`` objects over
all snapshot nodes or explicit seeds. See :doc:`typed-sampling` for memory,
restart, seed-round and reproducibility contracts. Consume batches rather than
collecting the full iterator. Existing walk methods and defaults are unchanged.

RWS-02: Skip-gram context pairs
----------------------------------------------------------------------

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
----------------------------------------------------------------------

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
----------------------------------------------------------------------

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

RWS-05: heterogeneous and finite-state walks
----------------------------------------------------------------------

Pass ``WalkPolicy(schema=WalkSchema(...))`` to the policy kernel. ``node_types``
restricts candidate target types; ``metapath`` is a cyclic compact relation-ID
sequence. ``relation_weights`` multiplies probabilities (unspecified relations
have weight one; zero disables a relation). Global ``relations`` intersects
schema restrictions. Unknown type/relation IDs fail before sampling, including
for empty seed lists.

Alternatively, supply ``transitions=[WalkTransition(...), ...]``. Each arc has
source/target state, optional relation, source/target node types, direction
relative to the current node, and non-negative weight. The arc's direction
replaces the global direction for that step. A missing outgoing arc is a dead
end. States and arcs are bounded by explicit ``max_states`` (default 256) and
``max_transitions`` (default 4096). Metapaths and state machines are mutually
exclusive. Both reset to their initial position/state on every restart.

Alternatives are (arc, edge) pairs: overlapping arcs deliberately count as
separate choices and may lead to different states. Arc and relation weights
multiply optional static edge weights (``WalkPolicy(strategy="weighted")``).
Types refer to ``snapshot.node_type_ids``, not native label IDs. Untyped -1
nodes do not match an explicit typed constraint. The default schema does not
implicitly expand hierarchies.

.. code-block:: python

   from gestaltdb.sampling import WalkPolicy, WalkSchema, WalkTransition

   policy = WalkPolicy(schema=WalkSchema(transitions=[
       WalkTransition(0, 1, relation=0, target_types=[1]),
       WalkTransition(1, 0, relation=1, direction="in", target_types=[0]),
   ]))
   assert policy.schema.initial_state == 0

RWS-06: chronological interval/event walks
----------------------------------------------------------------------

``WalkPolicy(chronology=TemporalWalkPolicy(...))`` requires a temporal snapshot.
It is separate from ordinary point/window eligibility filters, which remain in
force. ``order="nondecreasing"`` (default) or ``"nonincreasing"`` permits equal
times. ``time_selection="interval"`` chooses the earliest feasible microsecond
forward or the latest backward, inside edge validity AND the query window/point.
Backward interval selection requires an explicit ``initial_time`` because an
open-ended interval has no latest instant. ``time_selection="valid_start"``
instead treats the original edge-version valid start as an event; if that event
falls outside the query window it is excluded even if the interval overlaps.
This is deliberately not a silent equivalence between intervals and events.

``initial_time`` and ``reference_time`` accept UTC instants, aware datetimes or
integer epoch microseconds. ``max_gap_us`` limits absolute gaps from the current
clock, including the first step when an initial time is supplied. Half-open
validity excludes the end; backward latest selection uses end minus one
microsecond. Open ends impose no finite upper bound. Restart resets chronology
to the initial clock, and starts a new chronological segment.

Optional ``recency_half_life_us`` requires ``reference_time`` and multiplies
probabilities by 2 ** (-abs(time - reference) / half-life). It favors proximity
to that reference, not an implicit no-future rule. Relative scaling avoids total
underflow; sufficiently remote candidates may underflow to zero. Eligibility,
schema restrictions and avoidance are never relaxed to satisfy chronology.
Only edge-version validity is enforced; node-history availability is not an
additional implicit constraint.

The returned ``TemporalWalkBatch`` extends ordinary walk arrays with aligned
``transition_times_us``, ``transition_time_mask`` and ``edge_version_ids``.
Padding/restarts have a false mask and None version ID; zero is a valid epoch
time. ``source_provenance`` is copied from the loaded snapshot, not independently
reauthenticated during sampling; existing snapshot hydration/source verification
guarantees still apply. Legacy non-chronological batch formats are unchanged.

.. code-block:: python

   from gestaltdb.sampling import TemporalWalkPolicy, WalkPolicy

   policy = WalkPolicy(chronology=TemporalWalkPolicy(
       initial_time=0, max_gap_us=1000000, time_selection="interval",
   ))
   assert policy.chronology.order == "nondecreasing"

RWS-07: transition units and node2vec adjacency
----------------------------------------------------------------------

``WalkPolicy(transition_unit="edge")`` samples eligible (arc, edge) alternatives.
Parallel edges therefore affect endpoint probabilities. ``transition_unit="neighbor"``
first groups alternatives by endpoint; without weights it samples distinct
neighbors uniformly, then an eligible edge/arc uniformly within that neighbor.
With weights, ``neighbor_weight_aggregation="sum"`` (default), ``"mean"`` or
``"max"`` computes each neighbor's weight. The chosen neighbor's actual edge is
then sampled proportional to its alternative weights. Sum preserves parallel
edge mass; mean/max do not multiply a neighbor's weight simply for duplicate
equal-weight edges. A self-loop is one edge alternative under incident traversal,
not duplicated for its two endpoints. Batches always retain the selected edge.

``WalkPolicy(node2vec=True, return_factor=p, inout_factor=q)`` adds second-order
bias. Like the legacy node2vec method, its first step is uniform over alternatives
unless schema/recency weights apply; ``weighted_first_step=True`` additionally
opts into static first-step edge weights. Subsequent steps multiply static edge
weights by the return/in-out bias. ``strategy`` controls static weights for
non-node2vec policies, not node2vec's legacy first-step rule.

``bias_adjacency="snapshot"`` uses static unfiltered undirected adjacency, as
the legacy method does. ``"eligible"`` instead uses undirected adjacency under
the global relation filter, query validity filters, static schema target types
and relation weights, positive static weights, and the current chronological
clock when supplied. Avoidance history is not part of this adjacency definition.
Eligible adjacency fails closed for finite-state schemas and metapaths: a
path-state-dependent distance graph has no implicit meaning. Use snapshot
adjacency for those combinations. Both options leave legacy methods unchanged.

.. code-block:: python

   from gestaltdb.sampling import WalkPolicy

   uniform_neighbors = WalkPolicy(transition_unit="neighbor")
   weighted_neighbors = WalkPolicy(
       transition_unit="neighbor", strategy="weighted", neighbor_weight_aggregation="mean",
   )
   filtered_node2vec = WalkPolicy(
       node2vec=True, return_factor=2, inout_factor=0.5,
       bias_adjacency="eligible", weighted_first_step=True,
   )
   assert uniform_neighbors.transition_unit == weighted_neighbors.transition_unit

RWS-08: measured acceleration decision
----------------------------------------------------------------------

The pure-Python reference stays the runtime executor. Profiling identified schema
candidate construction, weighted choice and node2vec adjacency/``isin`` work as
major costs. The implementation avoids per-edge dictionary lookups when relation
weights are absent, and skips avoidance membership tests/copies when the forbidden
set is empty. These retain seeded parity and do not add another executor.

A bounded distribution-LRU prototype lives ONLY in ``benchmarks/walk_cache.py``.
It keys exact ordered candidate IDs and effective weight bytes, retaining at most
the configured entries and key/array payload bytes. It covers static first-order
distributions and bounded node2vec transition states without conflating filters,
clock states or changed weights. Tests cover evictions, oversize-entry bypass,
changed-weight keys, snapshot rebinding, temporal-filter/clock parity, and exact
seeded output. Neighbor-group choices bypass it. It is not a public runtime API.

Reproduce the screening experiment (no optional backend is needed):

.. code-block:: console

   uv run python -m benchmarks.random_walks --nodes 100 --edges 600 --walks 100 --length 10 --cache --profile
   uv run python -m benchmarks.random_walks --nodes 100 --edges 600 --walks 100 --length 10 --cache --mode memmap
   uv run python -m benchmarks.random_walks --nodes 100 --edges 12000 --walks 150 --length 10 --workload hub --cache

JSON includes actual transitions/walks per second, Python-traced peak allocation,
cumulative process peak RSS (Linux KiB), output bytes, exact output hashes, cache
hits/misses/evictions/payload, parameters, Python/NumPy versions and optional
profile hotspots. Both the legacy kernel and equivalent opt-in policy are checked
against cached output. Timing includes streaming result hashing and tracemalloc;
these are small single-run screening measurements, not statistical speed claims.
Python allocation tracing is not native NumPy/RSS accounting. Process RSS is a
cumulative high-water mark, not a per-case allocation measurement.

On the development host (Python 3.12.3, NumPy 2.5.2), seed 7, batch size 64,
4 MiB cache payload cap / 1024 entries:

.. csv-table:: Observed transitions/second, rounded
   :header: "Layout/workload", "Walk", "Legacy", "Policy reference", "Cache prototype"

   "RAM / uniform, 600 edges", First-order, 8980, 5116, 4793
   "RAM / uniform, 600 edges", Node2vec, 5224, 3780, 3522
   "RAM / hub, 600 edges", First-order, 8191, 4590, 4493
   "RAM / hub, 600 edges", Node2vec, 4516, 3280, 3199
   "Memmap / uniform, 600 edges", First-order, 8193, 4725, 4556
   "Memmap / uniform, 600 edges", Node2vec, 4813, 3627, 3307
   "Memmap / hub, 600 edges", First-order, 7393, 4388, 4283
   "Memmap / hub, 600 edges", Node2vec, 4121, 3076, 3018
   "RAM / hub, 12000 edges", First-order, 4128, 2683, 2542
   "RAM / hub, 12000 edges", Node2vec, 2077, 1687, 1621

Retained prototype payloads were 32–57 kB for small graphs and 661/991 kB for
the large hub first-order/node2vec runs. Python-traced peak allocations on that
hub grew from roughly 1.58/1.48 MB without cache to 2.23/2.55 MB with it.
High hit rates did not offset exact-key overhead: the cache was slower in every
screened case. **Decision:** do not adopt the cache or introduce native kernels
based on this evidence. Keep the bounded prototype for future workload-driven
experiments, coordinated with #123. Use the legacy kernels when no extended
policy is needed; policy composition itself has measurable overhead. Revisit
only with representative repeated measurements and unchanged correctness gates.
