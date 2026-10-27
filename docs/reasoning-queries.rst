Generation-pinned reasoning-aware Cypher
========================================

``gestaltdb.reasoning.adapters.queries.ReasoningQueryView`` is an explicit,
read-only Cypher surface over a selected logical graph. It prepares a complete
current-graph inference closure once using :doc:`reasoning-current-admission`
and ``open_inferred_view``. Queries reuse those facts; they never silently
run reasoning, write inferred edges, or change ordinary ``graph.query``.

Example (requires the ``lmdb`` extra)
-------------------------------------

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.ontology import Ontology, SubPropertyOf, Transitive, compile_ontology
   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.adapters.generation import catalog_for_ontology
   from gestaltdb.reasoning.adapters.graph import GraphMapping
   from gestaltdb.reasoning.adapters.queries import QuerySelection, ReasoningQueryView

   compiled = compile_ontology(Ontology("paths", [
       SubPropertyOf("edge", "reach"), Transitive("reach")]))
   mapping = GraphMapping(edge_types=(("LINK", "edge"),))
   selection = QuerySelection(edge_types=(("LINK", "edge"), ("REACH", "reach")))
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path + "/db", backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("a"), Node("b"), Node("c")])
           graph.put_edges_bulk([Edge("ab", "a", "b", {"type": "LINK"}),
                                 Edge("bc", "b", "c", {"type": "LINK"})])
           with graph.current_read_view(require_verifiable=True) as view:
               queries = ReasoningQueryView(
                   graph, view, compiled, catalog_for_ontology(compiled),
                   mapping=mapping, selection=selection, limits=ReasoningLimits())
               pin = queries.generation
               text = ('MATCH (a {id: $id})-[r:REACH]->(b) '
                       'RETURN id(b) AS target, r.reasoning_origin AS origin ORDER BY target')
               answer = queries.query(text, {"id": "a"}, expected_generation=pin,
                                      mode="asserted-plus-inferred")
               assert answer.result.records == [
                   {"target": "b", "origin": "inferred"},
                   {"target": "c", "origin": "inferred"}]
               assert answer.generation == pin
               assert answer.selection.digest == selection.digest
               assert queries.query(text, {"id": "a"}, expected_generation=pin,
                                    mode="asserted-only").result.records == []
           assert graph.query('MATCH (a)-[:REACH]->(b) RETURN b').records == []
       finally:
           graph.close()

Selection and logical graph semantics
-------------------------------------

Input ``GraphMapping`` controls which physical labels, relationship types and
entity-valued properties become base facts. Output ``QuerySelection`` is a
different policy: ``labels=(("Person", "class:Person"),)`` maps an output
Cypher label to a class ID; ``edge_types=(("REACH", "reach"),)`` maps an
output relationship type to a binary predicate. Names are exact; no implicit
hierarchy expansion occurs. Unselected facts are invisible; an absent selected
predicate simply yields no relationships. Type facts can only become labels.

Both modes query this **selected logical projection**, not a union with all
physical records. ``asserted-only`` uses mapped base facts;
``asserted-plus-inferred`` uses the complete closure, including base facts.
One selected fact/type pair yields one directed relationship; parallel source
edges collapse. If a fact is both asserted and derivable, its origin is
``asserted``. No proof or all-support claim follows from this origin field.

Nodes are subjects of selected type facts plus endpoints of selected relations.
Node IDs remain external entity IDs; endpoints need not have physical node
records (for example mapped entity-valued properties). Class IDs do not become
nodes merely because they appear in type facts. Unmapped isolated physical
nodes and arbitrary physical properties are not copied. Node properties are
empty; ``id(n)`` or ``{id: $id}`` addresses the external ID. Relationship
properties are ``type``, ``predicate`` and ``reasoning_origin``. Relationship
IDs use a deterministic ``logical:`` namespace derived from the fact and
output type, never physical edge IDs. Returned entities are invocation-local:
editing them cannot change later queries or source storage.

Supported queries
------------------

One ``MATCH`` with one node or fixed one-hop pattern; optional ``WHERE``; and
``RETURN`` with ordinary expressions, aggregates, aliases, ``DISTINCT``,
``ORDER BY``, ``SKIP`` and ``LIMIT``. Endpoint labels, label expressions,
``{id: ...}`` filters, relationship property maps, parameters, outgoing/incoming/
undirected patterns, and a one-hop path binding are supported. The adapter
reuses the existing parser, planner and runtime without modifying them.

Writes, DDL, procedures, multiple patterns/clauses, ``OPTIONAL MATCH``,
``WITH``, ``UNWIND``, unions, subqueries, temporal qualifiers, shortest paths,
multi-hop and variable-length traversal are rejected before execution. Ordinary
Cypher remains available separately for physical-record workflows. Typed n-ary
reasoning, temporal claims, range reasoning and persistent sessions are not
admitted by this current binary adapter.

Pins, lifetime and freshness
----------------------------

Keep both the live source ``graph`` and borrowed ``view`` open. The adapter
never closes either. Unlike the detached fact-only ``InferredGraphView``, this
surface requires its snapshot to remain active throughout preparation and
querying. A newly opened or unrelated snapshot is not silently substituted.

Each query requires an explicit mode and a full ``expected_generation``. Pins
include source database, snapshot sequence/token, ontology revision, input
mapping, program/catalog digests, semantics profile and dictionary identity.
Optional preparation-time ``expected_generation`` rejects incompatible reuse
before record extraction. The output selection has a separate canonical digest;
it does not alter reasoning identity. Results carry generation, selection and
mode alongside their ordinary ``QueryResult`` in ``answer.result``.

Active snapshot authentication and live-source freshness are checked before
and after query execution. Even unrelated source writes reject reuse; prepare
again under a new snapshot/generation. These checks detect observed movement;
they are not a writer lock, an atomic latest-state guarantee or a publication
permit. Snapshot hashing may be expensive. No caller-created closure/result
object can be supplied as trusted input to this adapter.

Bounds and resource costs
--------------------------

Preparation uses ``GraphSourceLimits`` and ``ReasoningLimits`` through the
existing adapter; base extraction is a second bounded pass over the same
snapshot. Closure computation uses the default indexed Python executor.

``QueryLimits`` caps projected nodes/relationships, cumulative logical candidate
reads/lookups per invocation, and final output rows. Exceeding a cap raises
``ReasoningLimitError`` with no successful partial result. Projection is rebuilt
per query, so all selected facts must fit even for a small ``LIMIT``. Candidate
property seeks use scans plus residual filters, not persistent indexes.
``max_result_rows`` is checked **after** runtime materialization, not a streaming
allocation guard. Single-pattern cardinality is also bounded by the projection.

Cancellation is cooperative during projection, logical record reads and
authentication boundaries. It does not interrupt snapshot hashing, sorting,
aggregates, scalar functions or native/backend calls mid-operation. Scalar
expressions such as large ``range``/``collect`` calls and caller parameters
retain ordinary allocation costs. These caps are not hard RSS, deadline or
untrusted-query sandbox guarantees. No optimizer, speedup or scale claim is
made; no heavy local benchmark is needed for this correctness-first surface.
