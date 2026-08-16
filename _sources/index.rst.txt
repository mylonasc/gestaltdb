GestaltDB Documentation
=======================

GestaltDB is a pure Python graph database toolkit for attributed graphs. It
stores nodes, edges, labels, typed adjacency records, and indexes on embedded
key-value backends such as LMDB, LevelDB, and RocksDB/PyRex.

Start with the quickstart if you want to create a graph and run a query. Use the
topic pages for backend selection, serializers, Cypher syntax, ingestion,
sampling, and benchmarks.

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   installation
   quickstart
   storage-backends
   serializers
   typed-sampling
   cypher
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
