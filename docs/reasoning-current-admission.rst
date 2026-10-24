Current-graph generation admission and execution
================================================

``gestaltdb.reasoning.adapters.graph_execution`` connects complete current-record
projection to shared generation admission and the explicit executor pipeline.
It does not accept caller triples, existing result objects or partial frontier
answers as complete input. Existing full projection and :doc:`reasoning-current-frontier`
APIs remain unchanged.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
   from gestaltdb.reasoning import Fact, ReasoningLimits
   from gestaltdb.reasoning.adapters.generation import catalog_for_ontology
   from gestaltdb.reasoning.adapters.graph import GraphMapping
   from gestaltdb.reasoning.adapters.graph_execution import bind_current_generation, execute_current_graph
   from gestaltdb.reasoning.executors import ExecutorRequest
   from gestaltdb.reasoning.plans import PreparedProgram
   from gestaltdb.reasoning.relational import CompilationIdentity

   compiled = compile_ontology(Ontology("relations", [SubPropertyOf("edge", "related")]))
   catalog = catalog_for_ontology(compiled)
   mapping = GraphMapping(edge_types=(("KNOWS", "edge"),))
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path, backend="lmdb", serializer="json")
       try:
           graph.put_nodes([Node("a"), Node("b")])
           graph.put_edge(Edge("ab", "a", "b", {"type": "KNOWS"}))
           with graph.current_read_view(require_verifiable=True) as view:
               binding = bind_current_generation(view, compiled, catalog, mapping=mapping)
               identity = CompilationIdentity(compiled.program.digest, binding.identity.context,
                   "external-strings-v1", "positive-binary-entity-v1", "indexed-scc-v1")
               plan = PreparedProgram(compiled.program, identity)
               result = execute_current_graph(view, compiled, catalog, mapping=mapping,
                   limits=ReasoningLimits(), expected_generation=binding.identity, plan=plan,
                   executor_request=ExecutorRequest(preferred="indexed-scc"))
               assert Fact("a", "related", "b") in result.outcome.result.facts
               assert result.outcome.telemetry.admission_digest == binding.identity.digest
               result.binding.verify_view(view)
           result.binding.verify_source(graph)
       finally:
           graph.close()

Identity and authentication
---------------------------

Storage-neutral ``CurrentSourceIdentity`` contains database ID, backend snapshot
sequence and provenance token. The token includes the complete state digest,
backend/serializer identity and snapshot kind. These fields are **not** temporal
commit horizon or valid time. ``GenerationIdentity`` accepts either source kind;
current generations use a distinct digest domain and every existing temporal
generation digest remains unchanged. Cross-kind compatibility always fails.

The adapter authenticates the caller-owned active snapshot with
``PinnedGraphSource``: snapshot backend/serializer metadata must match the
graph manifest independently, and serializer rebinding during reads is rejected.
It binds the actual compiled program, ontology revision,
full ``GraphMapping`` policy, catalog digest, positive binary entity profile and
``external-strings-v1`` representation. Exact single-rule catalog content must
match the compiled ontology; caller-supplied version IDs and ordinals remain
part of catalog identity, not independently authenticated rule publications.
An optional ``expected_generation`` must match **every** dimension before any
node/edge record is extracted. Matching metadata never bypasses authentication.

``bind_current_generation`` authenticates metadata without computing closure,
for example before constructing a prepared plan. Execution independently
authenticates again and reads complete canonical records; it never trusts the
binding as an input-completeness certificate. Constructing identity/binding
values manually is not authentication or proof of inference soundness.

``binding.verify_view(view)`` reauthenticates the exact active immutable
snapshot, including full-state hashing. Live source changes do not alter an
already open view. ``binding.verify_source(graph)`` instead checks that the
latest mutable source still matches; even unrelated source writes can reject
reuse conservatively. It does not reopen old snapshots. No historical-mode
flag invents an expired mutable snapshot, and neither check is an atomic
compare-and-publish permit.

Execution and bounds
--------------------

Omit ``executor_request`` to keep indexed Python. SCC requires explicit opt-in
and a compatible ``PreparedProgram``. Polars requires explicit supported
closure-only opt-in and an installed runtime; it never receives proof or
maintenance work through fallback. The dispatcher independently validates
actual program/context/profile/dictionary/executor compatibility and reports
shared ``StageTelemetry``. Witness-capable engines preserve exact snapshot-local
evidence references but select bounded witnesses, not all-support provenance.

``GraphSourceLimits.max_records`` bounds canonical candidate lookups including
unmapped records. ``ReasoningLimits`` bounds yielded evidence and evaluation
using each engine's documented accounting. Cancellation is checked between
record reads even when nothing maps to a fact. Snapshot authentication and
backend-internal work remain outside these counters; hashing/native calls are
not interrupted mid-call. Failures return no successful partial result and the
caller retains ownership of its view. The adapter performs no writes or repairs.

Complete canonical projection does not depend on secondary indexes, including
when those indexes are marked stale; this is not permission to use stale indexes
in the separate frontier adapter. Full-state authentication can dominate work
and uses memory; this integration makes no performance or constant-time claim.
LMDB provides verifiable snapshots; unsupported backend requests fail closed
rather than using unpinned reads.

This completes one current-graph admission/execution consumer, not persistent
cache migration, range/session/sampler admission, publication or general typed,
negation/equality/aggregate execution. Those require their own source and
semantic contracts; see :doc:`reasoning-generation-identity`.
