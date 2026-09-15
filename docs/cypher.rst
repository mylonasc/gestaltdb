Cypher Queries
==============

GestaltDB exposes an expanding, read-only openCypher subset through
``GraphDB.query(cypher, parameters=None)``. The grammar-based frontend supports
comments, Unicode and backtick-escaped names, source-located syntax errors, and
standard expression precedence. Execution remains focused on indexed node
scans, typed relationship expansion, filtering, ordering, and chained ``MATCH``
clauses.

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

Label scans use the label index. Inline property maps may contain multiple
entries. The first indexed property can select an index-backed candidate set;
all entries are then checked.

.. code-block:: python

   graph_db.create_node_property_index("name")

   graph_db.query('MATCH (n:Drug) RETURN n')
   graph_db.query('MATCH (n:Drug {name: "Aspirin"}) RETURN n.id')
   graph_db.query('MATCH (n:Drug {name: "Aspirin", approved: true}) RETURN n.id')
   graph_db.query('MATCH (n:Drug:Approved) RETURN n.id')
   graph_db.query('MATCH (n) RETURN n.id LIMIT 5')

Typed Relationship Traversal
----------------------------

Use an anchored pattern when you know the start node ID.

``{id: ...}`` on the first node of a relationship path is a GestaltDB extension
that addresses the stable entity ID; it accepts a string literal or parameter.
In standalone node patterns and on later path nodes, ``id`` is an ordinary
stored property name.

.. code-block:: python

   graph_db.query('MATCH (d {id: "drug-1"})-[:binds]->(p) RETURN p.id')
   graph_db.query('MATCH (p {id: "protein-1"})<-[:binds]-(d) RETURN d.id')
   graph_db.query('MATCH (n {id: "x"})-[:related]-(m) RETURN m.id')

Unanchored typed relationship scans are also supported.

.. code-block:: python

   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN a.id, r.id, b.id')
   graph_db.query('MATCH (a)-[r:binds|inhibits]->(b) RETURN r.id ORDER BY r.id')

General fixed-length patterns may be unanchored and may filter every node in the
path. Anonymous nodes and relationships, omitted relationship types, and
bracketless relationships are supported. Untyped expansion scans canonical edge
records and is therefore less efficient than typed adjacency expansion.

.. code-block:: python

   graph_db.query(
       'MATCH (a:Drug {approved: true})-[:binds]->()-[:affects]->(d:Disease {active: true}) '
       'RETURN a.id, d.id'
   )
   graph_db.query('MATCH (a)-->(b) RETURN a.id, b.id')
   graph_db.query('MATCH ()-[r]-(b) RETURN r.id, b.id')

Multiple comma-separated pattern parts share one ``MATCH`` scope. Shared
variables join the parts; disconnected parts form a Cartesian product.

.. code-block:: python

   graph_db.query(
       'MATCH (a:Drug)-[:binds]->(p), (p)-[:associated_with]->(d:Disease) '
       'RETURN a.id, d.id'
   )

Within one path or comma-separated ``MATCH``, one stored relationship cannot be
used twice. A subsequent ``MATCH`` clause starts a new relationship uniqueness
scope.

Filtering
---------

``WHERE`` supports property-to-property and property-to-value comparisons,
arithmetic, parentheses, ``NOT``, ``AND``, ``XOR``, ``OR``, ``IN``, ``IS
NULL``, ``IS NOT NULL``, ``STARTS WITH``, ``ENDS WITH``, ``CONTAINS``, and
regular-expression matching with ``=~``.

Predicates use Cypher three-valued logic. In particular, ``n.value = null`` and
``n.value <> null`` do not pass ``WHERE``; use ``IS NULL`` or ``IS NOT NULL``.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) WHERE n.name = "Aspirin" RETURN n.id')
   graph_db.query('MATCH (n:Person) WHERE n.age >= $age RETURN n.id', parameters={"age": 35})
   graph_db.query('MATCH (n) WHERE n.kind IN ["drug", "protein"] RETURN n.id')
   graph_db.query('MATCH (n) WHERE n.name IS NOT NULL RETURN n.id')
   graph_db.query('MATCH (n) WHERE n.name STARTS WITH "A" OR n.score * 2 >= n.minimum RETURN n.id')

Relationship predicates work in anchored traversals and unanchored relationship
scans. When an edge property index exists, exact and range predicates on typed
relationship scans can use the composite type/property index.

.. code-block:: python

   graph_db.create_edge_property_index("score")

   graph_db.query('MATCH (a)-[r:binds]->(b) WHERE r.score >= 0.8 RETURN r.id, b.id')

Projection and Result Shaping
-----------------------------

Use aliases, ``RETURN *``, ``DISTINCT``, ``ORDER BY``, ``SKIP``, and ``LIMIT``.
``RETURN`` and ``ORDER BY`` accept general expressions, including arithmetic,
literals, parameters, lists, and maps. Unaliased expressions use a
deterministic rendered column name, so prefer ``AS`` aliases for readability.
``ORDER BY`` accepts a projected alias. ``SKIP`` and ``LIMIT`` accept either a
non-negative integer literal or a parameter containing one.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) RETURN n.id AS id, n.name AS name ORDER BY name LIMIT $count', parameters={"count": 10})
   graph_db.query('MATCH (n) RETURN DISTINCT n.kind ORDER BY n.kind')
   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN * LIMIT 10')

GestaltDB retains these extension projections for entity metadata:

- ``n.id`` and ``r.id`` return entity IDs.
- ``n.labels`` returns node labels.
- ``r.source`` and ``r.target`` return relationship endpoints.
- Missing properties project as ``None``.

Chained MATCH Clauses
---------------------

Multiple ``MATCH`` clauses execute as a row pipeline. Reusing a variable enforces
that it refers to the same entity. ``WHERE`` may follow any ``MATCH`` clause
and filters that stage before later clauses run.

.. code-block:: python

   graph_db.query(
       'MATCH (d:Drug {name: "Aspirin"}) '
       'MATCH (d)-[r:binds]->(p) '
       'RETURN d.id, r.score, p.id'
   )

WITH and Variable Scope
-----------------------

``WITH`` projects intermediate values, replaces the variable scope with its
outputs, and may carry its own ``WHERE``, ``DISTINCT``, ``ORDER BY``,
``SKIP``, and ``LIMIT``. Only projected variables and aliases survive; later
``MATCH`` clauses may traverse from retained entity variables. Each ``MATCH``
starts a new relationship uniqueness scope.

.. code-block:: python

   graph_db.query(
       'MATCH (p:Person) '
       'WITH p ORDER BY p.age DESC LIMIT 10 '
       'MATCH (p)-[:member_of]->(t:Team) '
       'RETURN p.name AS name, t.name AS team'
   )

Sampling Procedure
------------------

GestaltDB also exposes typed path sampling through a project-specific procedure.
The procedure name and arguments are GestaltDB-specific; the ``CALL``/``YIELD``
clause family is part of Cypher. Procedure arguments are currently literal
lists and maps.

.. code-block:: python

   result = graph_db.query(
       'CALL pg.sample_typed_paths(["drug-1"], '
       '[{"edge_type": "binds", "direction": "out", "sample_size": 2}]) '
       'YIELD path RETURN path LIMIT 1'
   )

Current Limitations
-------------------

Syntax and semantic failures are ``ValueError`` subclasses with source
locations. The current Cypher API does not yet support:

- mutating queries such as ``CREATE``, ``SET``, ``DELETE``, or ``MERGE``
- aggregation such as ``count`` or ``collect``
- ``OPTIONAL MATCH``
- variable-length paths
- path values such as ``p = (a)-[:T]->(b)``
- general function calls, list comprehensions, map projections, or ``CASE``
- ``UNWIND``, ``UNION``, subqueries, or generic procedures
- relationship property maps and quantified path patterns
