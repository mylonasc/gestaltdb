Indexed demand over current property graphs
==============================================

``reasoning.adapters.graph_frontier.answer_current_graph`` borrows one
verifiable ``CurrentGraphReadView`` and reuses :doc:`reasoning-frontier`'s
positive binary ``Program``/``Atom`` scheduler. It uses the explicit
``GraphMapping`` projection from :doc:`ontology-graph`; temporal claims never
participate. There is no implicit Cypher rewrite, mutation or publication.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.ontology import Ontology, SubPropertyOf, Transitive, compile_ontology
   from gestaltdb.reasoning import Atom, Fact, ReasoningLimits, Variable
   from gestaltdb.reasoning.adapters.graph import GraphMapping
   from gestaltdb.reasoning.adapters.graph_frontier import answer_current_graph

   compiled = compile_ontology(Ontology("example", [SubPropertyOf("edge", "reach"), Transitive("reach")]))
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("a"), Node("b"), Node("c")])
           graph.put_edges_bulk([Edge("ab", "a", "b", {"type": "KNOWS"}),
                                 Edge("bc", "b", "c", {"type": "KNOWS"})])
           with graph.current_read_view(require_verifiable=True) as view:
               result = answer_current_graph(
                   view, compiled.program, Atom("a", "reach", Variable("neighbor")),
                   ontology_revision=compiled.revision_id,
                   mapping=GraphMapping(edge_types=(("KNOWS", "edge"),)),
                   limits=ReasoningLimits(),
               )
           assert result.answer.facts == (Fact("a", "reach", "b"), Fact("a", "reach", "c"))
           assert len(result.answer.evidence) == 2
       finally:
           graph.close()

Projection and access plans
---------------------------

Mapped labels produce class facts, relationship types produce binary relations,
and mapped node properties must hold single nonempty entity strings. Parallel
edges collapse logically but retain separate snapshot-local evidence references.
Every mapped contributor to a requested predicate participates, including
assertions for predicates that also have rule heads.

Requests follow newly discovered bindings, not just the initial seed:

* Labels: bound subjects use node lookup; otherwise use label indexes, restricted
  to the requested class when the object is bound.
* Properties: bound subjects use node lookup. Configured properties use exact
  indexes for bound objects and property-prefix indexes otherwise. Without an
  index, a complete node-record scan is reported as ``property-record-scan``.
  The adapter never creates an index or silently drops answers.
* Relationships: bound subjects use outgoing typed adjacency; object-only
  bindings use incoming adjacency; unbound requests use relationship-type
  indexes. Canonical records are decoded before mapping, and both-bound
  requests filter the other endpoint.

Disconnected joins can require unbound reads. ``accesses`` reports candidate
lookup counts by strategy; ``records_read`` includes repeated visits, missing
direct lookups and filtered records, not backend-internal I/O or distinct IDs.
Evidence uses the same conventions as full ``PinnedGraphSource`` projection;
references are not temporal version IDs or independently verified proofs.

Authentication and bounds
-------------------------

The source verifies the immutable snapshot state digest and checks exact
view/store/provenance and active snapshot identity around each record read.
Live mutations cannot change that transaction's input. Expired/rebound views,
forged provenance and stale label/property/type indexes fail closed; rebuild
outside the view. Normal maintained index invariants are required: authentication
of stored bytes is not an independent index-completeness proof. Unreachable
records' mapped values are not independently audited.

LMDB supplies the required snapshot. Other backends are admitted only when
their current-read API provides this contract; unsupported LevelDB and
unverifiable PyRex requests never fall back to unpinned reads.

``GraphSourceLimits.max_records`` bounds cumulative candidate lookups before
decoding. ``ReasoningLimits`` and ``max_requests`` retain cumulative scheduler
work/fact/evidence/pass/request bounds. Cancellation is cooperative between
calls, not inside state hashing, backend operations or blocked iterators.
Failures return no successful partial result and do not close the caller's view.
Full-state snapshot authentication is outside the record budget; these counters
are not hard byte/RSS/deadline limits.

Only positive binary entity programs are supported; select the existing
complete evaluators explicitly for typed/negation/equality/aggregate modes.
Answers are complete for the goal, not full-world closure or persistent
generation admission. Current-state ``context``/``provenance`` never become
temporal source horizons. Existing default evaluators remain unchanged.

Measurement
-----------

``python -m benchmarks.reasoning_current_frontier --rows 10000 --repeats 3``
compares fresh-worker frontier reads with full projection on an explicit LMDB
mapping and ontology. It checks analytical goal parity and records snapshot
authentication, total pipeline time, candidate counts and worker peak RSS.
OS cache is uncontrolled; the selective synthetic fixture and fixed worker
order do not establish a universal speedup.

Clean-source results at ``c340af4`` (three fresh workers per mode, CPython 3.13,
JSON/LMDB; raw reports ``benchmarks/results/reasoning-re350-*.json``):

.. list-table:: Median seconds and candidate record visits
   :header-rows: 1

   * - Nodes
     - Full total
     - Frontier total
     - Full candidates
     - Frontier candidates
   * - 10,000
     - 0.788
     - 0.369
     - 19,999
     - 9
   * - 100,000
     - 6.478
     - 1.015
     - 199,999
     - 9

Every worker returned the eight expected goal facts. The fixture contains an
eight-link reachable chain, a terminal self-loop and many disconnected
self-loops, with mapped labels and properties included in full projection.
At 100K nodes the frontier still spent about 0.364s opening/authenticating the
snapshot and 0.356s extracting/evaluating (including a second full-state digest).
Worker peak RSS medians were 513MB full / 199MB frontier, including imports and
authentication, not query-only memory. Candidate counts exclude that full-state
work: this demonstrates selective decoding savings, not constant-time end-to-end
queries, general graph speedups or automatic executor admission.
