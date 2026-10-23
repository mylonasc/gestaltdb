Snapshot consistency: staleness, appends and compaction
============================================================

``SamplerSnapshot`` directories are derived indexes: the durable graph
database stays the source of truth. This page documents how a snapshot
records where it came from, how to detect that the source moved on, and
how to evolve a snapshot without a full rebuild.

Consistency metadata
--------------------

Every ``build``/``from_arrays`` snapshot records:

* ``snapshot_id`` — a random hex identity for the logical snapshot. It is
  stable across appends and compactions and changes only on rebuild.
* ``source_version`` — the best observable source token at build time: an
  authenticated current-read-view token (backend sequence plus state
  digest) when the graph exposes verifiable read identity, otherwise
  ``None``. Backends without verifiable identity (plain LevelDB graphs)
  and graph-like doubles record ``None``: no counter is manufactured.
* ``schema_hash`` — a digest of the interpretation contract (type
  properties, layout, sampling policies), never of the data itself.
* ``append_generation`` / ``compactions`` — monotonic evolution counters.
* ``filtered`` — whether build-time node/edge filters were applied.

Stale checks
------------

.. code-block:: python

   if snapshot.is_stale(graph):
       snapshot = SamplerSnapshot.build(graph, path)  # or append/compact
   snapshot.check_fresh(graph)  # raises ValueError with both versions

A snapshot without a recorded source version (legacy builds, unverifiable
graphs) always reports stale: freshness that cannot be proven is never
assumed. Only an exact token match counts as fresh; plain temporal commit
counters are deliberately not consulted because they are blind to
non-temporal mutations.

Append-only evolution
---------------------

.. code-block:: python

   summary = snapshot.append_from_graph(graph, new_edge_keys)

Node and relation maps grow append-only: existing compact IDs never move,
so already-sampled batches stay valid. New endpoints resolve their node
type through the persisted type mapping when the snapshot carries one;
otherwise they encode as unknown (``-1``). Adjacency indexes rebuild from
the extended rows — ``O(E)`` work, so measure before assuming appends
beat rebuilds at scale.

Append requires snapshots built by ``build`` (the recorded type
properties must match the caller's, compared by schema hash) and rejects
temporal snapshots, duplicate external edge IDs, unresolvable edge keys,
and array-built snapshots without a recorded interpretation. After a
successful append the object refreshes in place, ``append_generation``
advances, and the source version is restamped. No live readers may hold
the snapshot path (including memmap loads) across the call.

Compaction policy
-----------------

.. code-block:: python

   snapshot.compact()          # canonical rebuild + integrity verification
   snapshot.compact(graph)     # additionally prune edges deleted from source

``compact`` rebuilds every index from the current rows, rewrites all
arrays atomically (staging directory, authenticated load, backup-swap
with restore), and re-verifies integrity. Node and relation IDs are never
reordered. With a graph, edge rows whose external ID no longer resolves
in the source are pruned first — pruned edge rows disappear, so edge
compact IDs after pruned rows may shift while node and relation IDs stay
put. Filtered snapshots reject graph pruning (recorded filters are
callables that cannot be re-applied) but still allow the no-graph
canonical rebuild.

Compact after bulk appends that may overlap, before publishing or sharing
a snapshot that underwent appends, or whenever source deletions must be
reflected without a full rebuild. Temporal snapshots are rejected;
rebuild them explicitly.
