Cypher Queries
==============

GestaltDB exposes an expanding openCypher subset through
``GraphDB.query(cypher, parameters=None)``. The grammar-based frontend supports
comments, Unicode and backtick-escaped names, source-located syntax errors, and
standard expression precedence. Execution covers indexed node and relationship
scans, typed relationship expansion, filtering, ordering, chained ``MATCH``
clauses, writes, and a persisted node-constraint catalog.

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
entries. The first eligible indexed property selects an index-backed candidate
set; exact and range predicates in ``WHERE`` reuse the same indexes, and all
predicates are then checked. Independent comma-separated node patterns start
with the smallest labeled scan.

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

``{id: ...}`` in any node pattern is a GestaltDB extension that addresses
the stable entity ID; it accepts a string literal or parameter. It composes
with labels, other properties, and relationship patterns, and keeps
``MERGE`` idempotent on every endpoint.

.. code-block:: python

   graph_db.query('MATCH (d {id: "drug-1"})-[:binds]->(p) RETURN p.id')
   graph_db.query('MATCH (p {id: "protein-1"})<-[:binds]-(d) RETURN d.id')
   graph_db.query('MATCH (n {id: "x"})-[:related]-(m) RETURN m.id')

Unanchored typed relationship scans are also supported.

.. code-block:: python

   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN a.id, r.id, b.id')
   graph_db.query('MATCH (a)-[r:binds|inhibits]->(b) RETURN r.id ORDER BY r.id')

Relationship patterns accept inline property maps with literal or parameter
values, which filter matched edges in both typed and untyped expansions. A
typed pattern can use a configured composite type/property index for an inline
property or eligible ``WHERE`` predicate.

.. code-block:: python

   graph_db.query('MATCH (a)-[r:binds {score: 0.9}]->(b) RETURN r.id, b.id')

Variable-length patterns (``-[*]->``, ``-[*1..3]->``, ``-[*..2]->``,
``-[*2..]->``, ``-[*2]->``) match trails of the given length range without
repeating relationships. Multi-edge matches bind the relationship variable
to a list of edges (``[]`` for zero-length matches such as ``*0..1``).

.. code-block:: python

   graph_db.query('MATCH (d {id: "drug-1"})-[:binds*1..3]->(p) RETURN p.id')

Shortest paths select minimal-length trails. ``MATCH SHORTEST [k]`` emits
up to ``k`` shortest matches (one by default), ``MATCH ANY SHORTEST``
behaves the same, and ``MATCH ALL SHORTEST`` emits every match at the
minimal length. ``shortestPath`` and ``allShortestPaths`` evaluate a
single-hop pattern between bound endpoints to path values (``None`` or
``[]`` when unreachable).

.. code-block:: python

   graph_db.query('MATCH SHORTEST (d {id: "drug-1"})-[*]->(p) RETURN p.id')

Named paths bind a whole match to a path value holding endpoint-to-endpoint
nodes and edges. Use ``nodes()``, ``relationships()``, and ``length()`` to
inspect path values; bound paths remain usable downstream like any variable.

.. code-block:: python

   graph_db.query('MATCH p = (d:Drug)-[:binds]->(t:Target) RETURN length(p) AS hops')

Bitemporal Matches
------------------

Append ``FOR VALID_TIME AS OF <expression>`` to ``MATCH`` or ``OPTIONAL
MATCH`` to query immutable temporal versions instead of mutable current graph
records. Add ``FOR SYSTEM_TIME AS OF <expression>`` to select what was known at
a historical system instant; a system-time qualifier requires a valid-time
qualifier. Both expressions must produce ``datetime`` values and may use
literals, parameters, and scalar functions, but not row variables or
aggregates.

.. code-block:: python

   result = graph_db.query(
       'MATCH (p:Person)-[r:WORKS_FOR]->(c:Company) '
       'FOR VALID_TIME AS OF datetime($validAt) '
       'FOR SYSTEM_TIME AS OF datetime($knownAt) '
       'RETURN c.name, versionId(r), validFrom(r), validTo(r), systemFrom(r)',
       parameters={"validAt": valid_at, "knownAt": known_at},
   )

Qualifiers are query-wide. Every chained or optional match, ``UNION`` branch,
correlated subquery, fixed or variable path, and shortest path uses one valid
instant and one read view captured at query start. Repeated qualifiers must
resolve to the same instant. Temporal queries are read-only and reject all
write clauses. ``versionId``, ``validFrom``, ``validTo``, and ``systemFrom``
return temporal version metadata, propagate null, and return null for entities
from unqualified current-graph queries; ``validTo`` is also null for an
open-ended interval.

Temporal matches use temporal node and endpoint catalogs plus typed temporal
adjacency. Deferred temporal writes therefore require
``rebuild_temporal_indexes()`` or ``rebuild_deferred_indexes()`` before they can
be queried. Queries without qualifiers retain current-graph behavior.

Writes
------

``CREATE`` builds nodes and relationships. Bound variables are reused while
fresh ones are created (nodes get a UUID unless the properties supply
``id``); created relationships need exactly one type. ``SET`` updates
properties (``n.prop = expr``), adds labels (``n:Label``), merges maps
(``n += map``), or replaces all properties (``n = map``). ``REMOVE`` drops
properties or labels. All updates copy on write, apply per input row with
snapshot input semantics, and treat ``None`` targets as a null-safe no-op.

.. code-block:: python

   graph_db.query('CREATE (d:Drug {id: "drug-9", name: "New"}) RETURN d.id')
   graph_db.query('MATCH (d {id: "drug-9"}) SET d.score = 0.9 RETURN d.score')

Write queries run inside a backend transaction when one is supported
(LMDB, transactional PyRex) and roll back on failure; other backends apply
best-effort direct writes. Later clauses in the same query always observe
earlier writes.

``DELETE`` removes nodes and relationships. Deleting a node with remaining
relationships fails unless ``DETACH DELETE`` cascades to incident edges.

.. code-block:: python

   graph_db.query('MATCH (d {id: "drug-9"}) DETACH DELETE d')

``MERGE`` matches a pattern or creates it when absent, running ``ON MATCH``
actions in the first case and ``ON CREATE`` actions in the second. It is
idempotent: repeating the same ``MERGE`` changes nothing after the first
execution.

.. code-block:: python

   graph_db.query('MERGE (d:Drug {id: "drug-9"}) ON CREATE SET d.score = 0 RETURN d.id')

``FOREACH`` runs write clauses per element of a list without changing the
row stream. Loop variables never escape, and entity bindings refresh
afterwards so later clauses observe loop writes.

.. code-block:: python

   graph_db.query('MATCH (d:Drug) FOREACH (tag IN ["a", "b"] | SET d.tag = tag) RETURN d.id')

Node Constraints
----------------

``CREATE CONSTRAINT`` registers a persisted single-label, single-property
``UNIQUE`` or ``IS NOT NULL`` constraint. Existing data is validated before the
catalog changes. Cypher ``CREATE``, ``MERGE``, ``SET``, and ``REMOVE`` validate
the final node state, including ``ON CREATE SET`` actions. ``SHOW CONSTRAINTS``
returns ``name``, ``type``, ``label``, and ``property`` columns, and
``DROP CONSTRAINT`` removes a definition by name.

.. code-block:: python

   graph_db.query(
       'CREATE CONSTRAINT drug_name FOR (d:Drug) REQUIRE d.name IS UNIQUE'
   )
   graph_db.query(
       'CREATE CONSTRAINT drug_source FOR (d:Drug) REQUIRE d.source IS NOT NULL'
   )
   constraints = graph_db.query('SHOW CONSTRAINTS')
   graph_db.query('DROP CONSTRAINT drug_source')

Constraints currently cover node label/property values written through Cypher;
direct object and columnar writes do not enforce the catalog.

``SHOW INDEX`` and ``SHOW INDEXES`` list configured property indexes in
deterministic node-then-relationship order. The result columns are
``entityType`` and ``properties``; automatic label, relationship-type,
composite, and range representations are not separate catalog entries.

.. code-block:: python

   indexes = graph_db.query('SHOW INDEXES')

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
Predicate contexts require Boolean or ``None`` values rather than coercing
numbers or strings. Membership in an empty list is always false, including
``null IN []``.

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

Extended Expressions
--------------------

``RETURN``, ``WITH``, ``ORDER BY``, and ``WHERE`` accept ``CASE``,
subscripts, slices, list comprehensions, ``reduce``, map projections,
quantified predicates, and ``exists`` over properties. Comprehension
variables are local to their expression and shadow outer variables of the
same name. Missing values propagate as ``None``.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) RETURN CASE WHEN n.score >= 0.8 THEN "hit" ELSE "miss" END AS call')
   graph_db.query('MATCH (n:Drug) RETURN n.synonyms[0] AS first, n.synonyms[1..3] AS rest')
   graph_db.query('MATCH (n:Drug) RETURN [s IN n.synonyms WHERE s STARTS WITH "A" | s] AS matches')
   graph_db.query('MATCH (n:Drug) RETURN reduce(total = 0, s IN n.scores | total + s) AS total')
   graph_db.query('MATCH (n:Drug) RETURN n{.*, name: n.name} AS drug')
   graph_db.query('MATCH (n:Drug) WHERE all(s IN n.scores WHERE s > 0) RETURN n.id')
   graph_db.query('MATCH (n:Drug) WHERE exists(n.score) RETURN n.id')

Aggregates must still be top-level projection expressions; they cannot hide
inside ``CASE`` or comprehensions. Pattern comprehensions and
``exists()`` with a pattern argument are not yet supported.

Scalar Functions
----------------

The engine provides the core openCypher scalar functions: ``coalesce``,
``id``/``elementId``, ``type``, ``labels``, ``startNode``/``endNode``,
``properties``, ``head``/``last``, ``size``/``length``, the ``toBoolean``/
``toInteger``/``toFloat``/``toString`` conversions, ``trim``/``lTrim``/
``rTrim``, ``toUpper``/``toLower``, ``replace``, ``split``, ``substring``,
``left``/``right``, ``abs``, ``ceil``, ``floor``, ``round``, ``sqrt``,
``pow``, ``sign``, ``exp``, ``log``, ``log10``, ``sin``, ``cos``, ``tan``,
``pi``, ``e``, ``rand``, ``randomUUID``, ``range``, ``reverse``, ``tail``,
and ``keys``.
Conversions return ``None`` for unconvertible inputs, most other functions
propagate ``None``, and misused functions raise typed errors. Scalar calls
group like any other non-aggregate projection expression.

.. code-block:: python

   graph_db.query('MATCH (n:Drug) RETURN toUpper(n.name) AS name, size(n.synonyms) AS total')
   graph_db.query('MATCH (a)-[r:binds]->(b) RETURN type(r) AS rel, startNode(r).name AS source')

Temporal Values
---------------

Cypher expressions support immutable, microsecond-precision ``date``, ``time``,
``localtime``, ``datetime``, ``localdatetime``, and exact ``duration`` values.
Each constructor takes one strict ISO string or component map. Constructors
propagate ``null``; they deliberately do not provide a no-argument current-clock
form, so repeated queries remain deterministic. ``datetime`` requires an
explicit numeric offset and normalizes to UTC. Named timezones and calendar
month/year durations are not supported.

.. code-block:: python

   result = graph_db.query(
       "UNWIND [1] AS x "
       "RETURN date('2025-01-31') + duration('P2D') AS due, "
       "datetime('2025-01-31T12:30:00+02:00').hour AS utc_hour"
   )

Temporal values support component access, same-category comparison,
``DISTINCT``, grouping, ``ORDER BY``, ``CASE``, collections, parameters, and
canonical ``toString`` conversion. Exact duration arithmetic is supported for
dates, times, and datetimes; date arithmetic requires whole days. Aware Python
``datetime``/``time`` parameters become offset temporal values, while naive
ones become local values; Python ``date`` and ``timedelta`` parameters are also
normalized.

These values persist recursively in node and relationship properties, including
nested lists and maps. Pickle, JSON, MessagePack, and Protobuf use one portable
tagged representation, and old untagged records remain readable. Configured
exact and range property indexes support temporal scalars; fixed-offset ``time``
values are indexed by their UTC-equivalent time so equality matches Cypher
semantics. Temporal graph views and historical ``MATCH`` remain separate from
these scalar values. Array-native sampler snapshots do not export arbitrary
graph properties.

.. code-block:: python

   graph_db.create_node_property_index("observed")
   graph_db.query(
       "CREATE (e:Event {id: 'e1', observed: datetime('2025-01-31T12:30:00Z')})"
   )
   graph_db.query(
       "MATCH (e:Event) WHERE e.observed >= datetime('2025-01-01T00:00:00Z') "
       "RETURN e.id, e.observed"
   )

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

OPTIONAL MATCH
--------------

``OPTIONAL MATCH`` works as a left-outer join: input rows without a match
survive with newly introduced variables bound to ``None``. A ``WHERE`` clause
after ``OPTIONAL MATCH`` filters the joined rows, so null-extended rows are
removed unless the predicate keeps them (for example with ``OR m IS NULL``).
Later ``MATCH`` clauses can still traverse from bound variables, while
patterns referencing null-bound variables match nothing; unrelated patterns
form Cartesian products as usual. Aggregates ignore ``None`` inputs except
for ``count(*)``.

.. code-block:: python

   graph_db.query(
       'MATCH (d:Drug) '
       'OPTIONAL MATCH (d)-[:binds]->(p:Protein) '
       'RETURN d.id, p.id'
   )

UNWIND
------

``UNWIND`` expands each input row into one row per element of a list
expression, which may come from a literal, a parameter, or a bound variable.
``None`` and empty lists produce no rows. ``UNWIND`` may open a query or
appear after ``MATCH``, ``OPTIONAL MATCH``, or ``WITH`` stages.

.. code-block:: python

   graph_db.query('UNWIND ["Aspirin", "Ibuprofen"] AS name RETURN name')

UNION
-----

``UNION`` and ``UNION ALL`` combine the results of two or more independent
read queries. Every branch must return exactly the same columns in the same
order; branch ``ORDER BY``/``SKIP``/``LIMIT`` apply within their branch.
``UNION ALL`` concatenates branch results in branch order, while each
``UNION`` step deduplicates everything accumulated so far, preserving
first-seen order. Mixed operators fold left.

.. code-block:: python

   graph_db.query(
       'MATCH (d:Drug) RETURN d.id AS id '
       'UNION MATCH (p:Protein) RETURN p.id AS id'
   )

CALL Subqueries
---------------

``CALL { ... }`` runs a correlated subquery once per input row. The inner
query sees outer bindings, and its ``RETURN`` outputs merge into the outer
row (shadowing same-named variables); outer rows with an empty subquery
result are dropped. Parameters work everywhere, including ``SKIP``/``LIMIT``,
regular expressions, ``IN`` lists, and inside subqueries.

.. code-block:: python

   graph_db.query(
       'MATCH (d:Drug) '
       'CALL { MATCH (d)-[:binds]->(p:Protein) RETURN p } '
       'RETURN d.id, p.id'
   )

Registered Procedures
---------------------

Top-level registered procedure calls use ``CALL qualified.name(...) YIELD``.
Yielded fields may be aliased, arguments may use parameters, and unknown
procedure names or fields produce source-located semantic errors. Generalized
syntax does not enable arbitrary dispatch. ``pg.sample_typed_paths`` and the
bounded temporal-epistemic ``kg.entails`` procedure are registered.

.. code-block:: python

   result = graph_db.query(
       'CALL pg.sample_typed_paths($seeds, $pattern) '
       'YIELD path AS sampled RETURN sampled LIMIT 1',
       parameters={
           "seeds": ["drug-1"],
           "pattern": [{"edge_type": "binds", "direction": "out", "sample_size": 2}],
       },
   )

``kg.entails`` accepts agent, proposition, modal operator, and options. It can
yield ``status``, ``confidence``, and ``explanation``. See
:doc:`modal-epistemic` for its world, temporal, formula, and limit semantics.

Aggregation and Implicit Grouping
---------------------------------

``count``, ``collect``, ``sum``, ``avg``, ``min``, and ``max`` work in
``WITH`` and ``RETURN``, including ``count(*)`` and argument-level
``DISTINCT`` such as ``count(DISTINCT n.age)``. Non-aggregate projection
expressions become implicit grouping keys; an all-aggregate projection has
one global group. Null inputs are ignored except by ``count(*)``, and empty
global inputs return ``0`` for ``count``, ``[]`` for ``collect``, ``0`` for
``sum``, and ``None`` for ``avg``, ``min``, and ``max``.

.. code-block:: python

   graph_db.query('MATCH (p:Person) RETURN count(*)')
   graph_db.query('MATCH (p:Person) RETURN p.department AS department, count(*) AS total ORDER BY total DESC')

``ORDER BY`` in an aggregate query must reference projected outputs.
Aggregates cannot appear in ``WHERE`` (filter an aggregating ``WITH`` instead),
and cannot nest. Aggregate results may be composed with scalar expressions and
functions, such as ``count(*) + 1`` or ``toString(count(*))``.

``ORDER BY`` is stable and applies Cypher's value hierarchy across supported
maps, entities, lists, paths, strings, Booleans, numbers, and nulls. Nulls sort
last ascending and first descending. Conversion functions reject unsupported
input types; malformed strings passed to numeric/Boolean conversions return
``None``. Temporal constructors (such as ``date`` and ``duration``) and the
spatial ``point`` constructor produce explicit unsupported-function errors.

Current Limitations
-------------------

Syntax and semantic failures are ``ValueError`` subclasses with source
locations. The current Cypher API does not yet support:

- mutating clauses beyond ``CREATE``, ``SET``, ``REMOVE``, ``DELETE``, ``MERGE``, and ``FOREACH``
- relationship, multi-property, and direct-object-write constraint enforcement
- pattern comprehensions and ``exists()`` with a pattern argument
- GQL quantified relationships/path patterns; errors suggest legacy ``*min..max`` syntax
- scalar functions beyond the documented core set
- procedures other than the registered ``pg.sample_typed_paths`` and ``kg.entails`` calls
