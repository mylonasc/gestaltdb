Quickstart
==========

This page shows the shortest path from an empty database to a useful query.

Create a Graph
--------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import PickleSerializer

   graph_db = GraphDB(LMDBStore(path="quickstart_lmdb"), PickleSerializer())

   graph_db.put_node(Node(
       node_id="drug-1",
       labels=["Drug"],
       properties={"name": "Aspirin"},
   ))
   graph_db.put_node(Node(
       node_id="protein-1",
       labels=["Protein"],
       properties={"name": "PTGS1"},
   ))
   graph_db.put_edge(Edge(
       edge_id="drug-1-protein-1",
       source="drug-1",
       target="protein-1",
       properties={"type": "binds", "score": 0.9},
   ))

Nodes and Edges
---------------

Fetch records directly when you know their IDs:

.. code-block:: python

   drug = graph_db.get_node(b"drug-1")
   edge = graph_db.get_edge(b"drug-1-protein-1")

   print(drug.properties["name"])
   print(edge.properties["score"])

Labels and Indexes
------------------

Labels are indexed automatically. Property indexes are explicit; create them for
properties you query frequently.

.. code-block:: python

   graph_db.create_node_property_index("name")
   graph_db.create_edge_property_index("score")

   drugs = graph_db.nodes_by_label("Drug")
   aspirin = graph_db.nodes_by_property("name", "Aspirin")
   strong_edges = graph_db.edges_by_property_range("score", 0.8, None)

Cypher Query
------------

Use ``GraphDB.query`` for read-only Cypher queries over labels, typed
relationships, filters, ordering, and chained ``MATCH`` clauses.

.. code-block:: python

   result = graph_db.query(
       'MATCH (d:Drug {name: "Aspirin"}) '
       'MATCH (d)-[r:binds]->(p) '
       'WHERE r.score >= 0.8 '
       'RETURN d.id AS drug, p.name AS protein, r.score AS score'
   )

   for record in result:
       print(record)

Typed Traversal
---------------

Relationship types are stored in ``edge.properties["type"]`` and maintained in
typed adjacency indexes.

.. code-block:: python

   neighbors = graph_db.neighbors_by_edge_type("drug-1", "binds", direction="out")
   print(neighbors)

Bulk Inserts
------------

Use bulk writes when loading many records.

.. code-block:: python

   graph_db.put_nodes([
       Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen"}),
       Node(node_id="protein-2", labels=["Protein"], properties={"name": "PTGS2"}),
   ])
   graph_db.put_edges_bulk([
       Edge(edge_id="drug-2-protein-2", source="drug-2", target="protein-2", properties={"type": "binds"}),
   ])

Close the Store
---------------

Close the database when a script or notebook cell is finished.

.. code-block:: python

   graph_db.close()

Next Steps
----------

- See :doc:`cypher` for the supported query syntax.
- See :doc:`storage-backends` for backend selection.
- See :doc:`typed-sampling` for path and subgraph sampling.
- See :doc:`performance` for benchmark scripts and caveats.
