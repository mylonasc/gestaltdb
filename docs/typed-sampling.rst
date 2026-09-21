Typed Traversal and Sampling
============================

Typed traversal uses ``edge.properties["type"]``. When an edge has a type,
GestaltDB stores typed adjacency records for efficient directional scans.

Create a Typed Graph
--------------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import PickleSerializer

   graph_db = GraphDB(LMDBStore(path="typed_graph_lmdb"), PickleSerializer())

   for node_id, kind in [
       ("drug-1", "drug"),
       ("protein-1", "protein"),
       ("protein-2", "protein"),
       ("disease-1", "disease"),
   ]:
       graph_db.put_node(Node(node_id=node_id, properties={"kind": kind}))

   graph_db.put_edges_bulk([
       Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"}),
       Edge(edge_id="d1-p2", source="drug-1", target="protein-2", properties={"type": "drug-to-protein"}),
       Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "protein-to-disease"}),
   ])

Query Typed Neighbors
---------------------

.. code-block:: python

   proteins = graph_db.neighbors_by_edge_type(
       "drug-1",
       "drug-to-protein",
       direction="out",
   )
   print(proteins)

Query Typed Edges
-----------------

.. code-block:: python

   edge_ids = graph_db.edges_by_edge_type(
       "drug-1",
       "drug-to-protein",
       direction="out",
   )

Sample Neighbors
----------------

``sample_neighbors`` uses reservoir sampling, so memory is bounded by
``sample_size`` instead of by node degree.

.. code-block:: python

   import random

   sample = graph_db.sample_neighbors(
       "drug-1",
       "drug-to-protein",
       direction="out",
       sample_size=1,
       rng=random.Random(7),
   )

Object-Based Sampling Patterns
------------------------------

Use ``SamplingHop`` and ``SamplingPattern`` for validated, documented sampling
configuration objects.

.. code-block:: python

   import random

   from gestaltdb.sampling import SamplingHop, SamplingPattern

   pattern = SamplingPattern([
       SamplingHop("drug-to-protein", direction="out", sample_size=2),
       SamplingHop("protein-to-disease", direction="out", sample_size=1),
   ])

   paths = graph_db.sample_typed_paths(
       seed_ids=["drug-1"],
       pattern=pattern,
       rng=random.Random(3),
   )

Dictionary-Based Sampling Patterns
----------------------------------

Existing dictionary configurations are still supported.

.. code-block:: python

   pattern = [
       {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2},
       {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
   ]

   paths = graph_db.sample_typed_paths(["drug-1"], pattern)

Cypher Sampling Procedure
-------------------------

The Cypher API exposes multi-hop typed path sampling through a GestaltDB-specific
procedure call. This delegates to ``GraphDB.sample_typed_paths`` and returns one
``path`` value per sampled path.

.. code-block:: python

   result = graph_db.query(
       'CALL pg.sample_typed_paths(["drug-1"], '
       '[{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}, '
       '{"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1}]) '
       'YIELD path RETURN path'
   )

   for record in result:
       print(record["path"])

Sample a Materialized Subgraph
------------------------------

.. code-block:: python

   subgraph = graph_db.sample_typed_subgraph(
       seed_ids=["drug-1"],
       pattern=pattern,
   )

   print(subgraph["nodes"].keys())
   print(subgraph["edges"].keys())
   print(subgraph["paths"])

Rebuild Typed Adjacency
-----------------------

If edge records already exist but typed adjacency indexes are missing, rebuild
them from stored edges.

.. code-block:: python

   rebuilt = graph_db.rebuild_typed_adjacency()
   print(f"rebuilt {rebuilt} typed adjacency records")

Temporal Sampler Snapshots
--------------------------

Format-v2 snapshots can preserve the edge history visible at one system-time
horizon. Each compact edge row has an edge-version ID, half-open valid interval,
open-end mask, commit ID, and system time. The temporal outgoing and incoming
CSR indexes are ordered by endpoint, relation, and valid-time start.

.. code-block:: python

   snapshot = graph_db.build_sampler_snapshot(
       "snapshots/history",
       temporal=True,
       system_time="2026-01-01T00:00:00Z",
       time_bucket="day",
   )

   assert snapshot.temporal
   print(snapshot.edge_version_ids)
   print(snapshot.valid_from_us, snapshot.valid_to_us, snapshot.valid_to_open)

The build captures exactly one authenticated ``GraphReadView``. Corrections and
retractions are resolved at its system horizon, including splitting a surviving
version into multiple edge rows when only part of its validity is retracted.
``temporal_candidates(node, relation, valid_time, direction=...)`` uses the
start-time ordering to return a candidate superset; exact interval filtering and
temporal sampling are reserved for TKG-08.

V2 publication is atomic: arrays and canonical metadata are written in a sibling
staging directory, checksummed, and followed by ``completion.json`` before the
directory is renamed into place. Loading in RAM or memmap mode validates the
completion record, artifact catalog, SHA-256 checksums, dtypes, shapes, aligned
lengths, CSR bounds, interval invariants, and source-provenance token. Existing
format-v1 snapshots still load, but have no integrity or temporal guarantees.
