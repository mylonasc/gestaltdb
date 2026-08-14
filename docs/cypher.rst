Cypher Queries
==============

GestaltDB exposes a read-only Cypher subset through
``GraphDB.query(cypher, parameters=None)``. It is designed around the features
GestaltDB can execute efficiently today: indexed node scans, typed relationship
expansion, filtering, ordering, and chained ``MATCH`` clauses.

Relationship types come from ``edge.properties["type"]``. Node labels are stored
on ``Node(labels=[...])``.

Basic Result Shape
------------------

``GraphDB.query`` returns a result with ``columns`` and ``records``. Iterating the
result yields record dictionaries.

.. code-block:: python

   result = graph_db.query('MATCH (n:Drug) RETURN n.id, n.name LIMIT 10')

   print(result.columns)
   for record in result:
       print(record["n.id"], record["n.name"])

Node Scans
----------

Label scans use the label index. Inline properties are filtered and can use a
registered property index.

.. code-block:: python

   graph_db.create_node_property_index("name")

   graph_db.query('MATCH (n:Drug) RETURN n')
   graph_db.query('MATCH (n:Drug {name: "Aspirin"}) RETURN n.id')
   graph_db.query('MATCH (n:Drug:Approved) RETURN n.id')
   graph_db.query('MATCH (n) RETURN n.id LIMIT 5')

Typed Relationship Traversal
----------------------------

Use an anchored pattern when you know the start node ID.

.. code-block:: python

   graph_db.query('MATCH (d {id: "drug-1"})-[:binds]->(p) RETURN p.id')
   graph_db.query('MATCH (p {id: "protein-1"})<-[:binds]-(d) RETURN d.id')
   graph_db.query('MATCH (n {id: "x"})-[:related]-(m) RETURN m.id')

Unanchored typed relationship scans are also supported.

.. code-block:: python

   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN a.id, r.id, b.id')
   graph_db.query('MATCH (a)-[r:binds|inhibits]->(b) RETURN r.id ORDER BY r.id')

Filtering
---------

``WHERE`` supports equality, inequality, ordered comparisons, ``AND``, ``IN``,
``IS NULL``, and ``IS NOT NULL`` for property references.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) WHERE n.name = "Aspirin" RETURN n.id')
   graph_db.query('MATCH (n:Person) WHERE n.age >= $age RETURN n.id', parameters={"age": 35})
   graph_db.query('MATCH (n) WHERE n.kind IN ["drug", "protein"] RETURN n.id')
   graph_db.query('MATCH (n) WHERE n.name IS NOT NULL RETURN n.id')

Relationship predicates work in anchored traversals and unanchored relationship
scans. When an edge property index exists, exact and range predicates on typed
relationship scans can use the composite type/property index.

.. code-block:: python

   graph_db.create_edge_property_index("score")

   graph_db.query('MATCH (a)-[r:binds]->(b) WHERE r.score >= 0.8 RETURN r.id, b.id')

Projection and Result Shaping
-----------------------------

Use aliases, ``RETURN *``, ``DISTINCT``, ``ORDER BY``, ``SKIP``, and ``LIMIT``.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) RETURN n.id AS id, n.name AS name ORDER BY name')
   graph_db.query('MATCH (n) RETURN DISTINCT n.kind ORDER BY n.kind')
   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN * LIMIT 10')

Special projections prefer entity identity over user properties:

- ``n.id`` and ``r.id`` return entity IDs.
- ``n.labels`` returns node labels.
- ``r.source`` and ``r.target`` return relationship endpoints.
- Missing properties project as ``None``.

Chained MATCH Clauses
---------------------

Multiple ``MATCH`` clauses execute as a row pipeline. Reusing a variable enforces
that it refers to the same entity.

.. code-block:: python

   graph_db.query(
       'MATCH (d:Drug {name: "Aspirin"}) '
       'MATCH (d)-[r:binds]->(p) '
       'RETURN d.id, r.score, p.id'
   )

Sampling Procedure
------------------

GestaltDB also exposes typed path sampling through a project-specific procedure.
This is not standard openCypher syntax.

.. code-block:: python

   result = graph_db.query(
       'CALL pg.sample_typed_paths(["drug-1"], '
       '[{"edge_type": "binds", "direction": "out", "sample_size": 2}]) '
       'YIELD path RETURN path LIMIT 1'
   )

Current Limitations
-------------------

Unsupported syntax raises ``ValueError``. The current Cypher API does not yet
support:

- mutating queries such as ``CREATE``, ``SET``, ``DELETE``, or ``MERGE``
- aggregation such as ``count`` or ``collect``
- ``WITH``
- ``OPTIONAL MATCH``
- variable-length paths
- multiple pattern parts inside one ``MATCH`` clause
- path values such as ``p = (a)-[:T]->(b)``
