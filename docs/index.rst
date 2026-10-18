GestaltDB Documentation
=======================

GestaltDB combines an embedded property graph with **high-performance graph
sampling** and **indexed property-graph filtering**. Store attributed nodes and
edges on LMDB, LevelDB, or RocksDB/PyRex; select cohorts by labels, relationship
types, properties, and Cypher; then sample live neighborhoods or reusable
array-native snapshots for ML training.

Start with the quickstart if you want to create a graph and run a query. Use the
topic pages for backend selection, serializers, Cypher syntax, ingestion,
sampling, and benchmarks.

Filter, Sample, Train
-------------------------

- **Filter the property graph:** exact/range indexes, label + property and
  relationship type + property lookups, ID-only candidate iteration, and Cypher.
  See :doc:`property-filtering` for a complete filtered-snapshot workflow.
- **Sample efficiently:** live typed neighbors/paths/subgraphs, or compact CSR
  snapshots for node/edge seeds, weighted neighbors, heterogeneous layers,
  random/metapath walks, multihop subgraphs, and hard negatives.
  See :doc:`typed-sampling` for policies and runnable examples.
- **Reuse arrays across batches:** RAM or memory-mapped loading avoids repeated
  KV reads and graph-object deserialization during sampling. Temporal snapshots
  add point/window filtering and causal multihop policies. See :doc:`performance`
  for cost boundaries, memory trade-offs, and reproducible benchmarks.

.. toctree::
   :maxdepth: 2
   :caption: Filtering and Sampling

   property-filtering
   typed-sampling

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   installation
   quickstart
   storage-backends
   serializers
   operations
   temporal-versioning
   modal-epistemic
   cypher
   visualization
   performance
   notebooks

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api

Quick Example
-------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import PickleSerializer

   graph_db = GraphDB(LMDBStore(path="graph_lmdb_example"), PickleSerializer())

   alice = Node(node_id="alice", labels=["Person"], properties={"name": "Alice"})
   bob = Node(node_id="bob", labels=["Person"], properties={"name": "Bob"})
   graph_db.put_node(alice)
   graph_db.put_node(bob)

   graph_db.put_edge(Edge(
       edge_id="alice-knows-bob",
       source="alice",
       target="bob",
       properties={"type": "knows", "since": 2024},
   ))

   result = graph_db.query('MATCH (a:Person {name: "Alice"}) MATCH (a)-[:knows]->(b) RETURN a.id, b.name')
   print(result.records)

   graph_db.close()
