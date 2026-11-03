Sample historical graph structure
==================================

Use a temporal snapshot when a draw must respect when nodes and relationships
were available. A valid-time filter selects modeled time; a system-time horizon
selects which recorded history the build knows. These are separate dimensions
(:term:`bitemporal`; see :doc:`temporal-introduction` for a two-horizon timeline).
Recording temporal versions does not run temporal reasoning
or train a model. Start with :doc:`sampling-introduction` for snapshot basics.

Draw at a boundary and across a window
---------------------------------------

This complete database example uses integer UTC microseconds for a tiny timeline.
``a -> b`` holds on ``[0, 10)`` and ``a -> c`` on ``[10, 20)``. The endpoint
``10`` belongs only to the second interval. Nodes are available on ``[0, 30)``.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.sampling import SamplerEngine
   from gestaltdb.temporal import TemporalContext
   from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           graph.temporal.commit_versions([
               *(NodeVersionWrite.assertion(Node(name), (0, 30)) for name in ("a", "b", "c")),
               EdgeVersionWrite.assertion(Edge("ab", "a", "b", properties={"type": "R"}), (0, 10)),
               EdgeVersionWrite.assertion(Edge("ac", "a", "c", properties={"type": "R"}), (10, 20)),
           ])
           snapshot = graph.build_sampler_snapshot(
               f"{root}/history", temporal=True, time_bucket="none",
           )
       finally:
           graph.close()

       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       point = engine.sample_neighbors_external(
           ["a"], 10, relations=["R"], temporal=TemporalContext.as_of(10),
       )
       assert list(point.to_external(engine.snapshot)["neighbor_nodes"]) == ["c"]
       assert len(point.edge_version_ids) == len(point.edge_indices) == 1
       window = TemporalContext.during(5, 15)
       overlap = engine.sample_neighbors_external(["a"], 10, temporal=window)
       contained = engine.sample_neighbors_external(
           ["a"], 10, temporal=window, window_policy="contained",
       )
       assert set(overlap.to_external(engine.snapshot)["neighbor_nodes"]) == {"b", "c"}
       assert contained.edge_indices.size == 0

``overlap`` (the default) requires a nonempty intersection. ``contained`` requires
the entire edge interval inside the query window, so neither edge above qualifies.
Temporal, relation and direction filters run **before** fanout selection. Filtering
after a draw is not equivalent and can reduce the effective fanout.

Avoid time leakage deliberately
--------------------------------

- The build pins one authenticated recorded-history horizon. Supply
  ``system_time=...`` to build from a chosen historical horizon rather than the
  current visible prefix. Later database writes do not change the artifact.
- ``sample_nodes(..., temporal=...)`` filters node availability;
  ``sample_edges(..., temporal=...)`` filters edge-version rows. A repeated external
  edge ID can refer to multiple temporal rows; use compact rows when ambiguous.
- ``sample_multihop`` and ``sample_subgraph`` accept valid-start ordering through
  ``causal_policy="nondecreasing"`` or ``"nonincreasing"``. That is not the
  interval/event clock policy for walks; use :doc:`random-walks` for those clocks.
- Temporal neighbor/subgraph outputs retain aligned version IDs and validity
  arrays. Supplying temporal filters to a static/legacy snapshot fails explicitly.
- Time-aware negative rejection and future-candidate eligibility are separate
  choices. Read :doc:`sampling-negatives` before using historical positives for
  training; a point-filtered positive does not by itself define every negative
  policy.

The artifact format, integrity checks and compatibility boundaries remain in
:ref:`sampling-temporal-reference` and :doc:`sampling-consistency`. These guarantees
do not imply constant memory or lower latency than a static draw.
