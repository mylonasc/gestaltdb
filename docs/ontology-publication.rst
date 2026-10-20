Explicit point publication
==========================

``gestaltdb.reasoning.adapters.publication.reconcile_point`` is an opt-in
persistence boundary, separate from inference. It recomputes from fresh positive
entity claims under the database-wide temporal writer lock, then publishes one
logical temporal commit into a caller-selected, separate target world. It never
accepts an old inference result, changes current property-graph records or
installs generated rules in the global rule catalog.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.ontology import Ontology, SubPropertyOf, compile_ontology
   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
   from gestaltdb.reasoning.adapters.publication import reconcile_point

   plan = compile_ontology(Ontology("relations", [SubPropertyOf("p", "q")]))
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.assert_claim(subject="a", predicate="p", object="b", agent="registry",
                              polarity="positive", valid=(0, 100))
           params = dict(valid_time=50, source_world="default", target_world="derived",
                         publication_key="my-job", selection=ClaimSelection(),
                         mapping=ClaimMapping(), limits=ReasoningLimits())
           first = reconcile_point(graph, plan, **params)
           assert first.asserted == 2  # complete closure, including input p
           assert reconcile_point(graph, plan, **params).commit_id is None
           assert not list(graph.iter_claims_as_of(world="derived", valid_time=51))
       finally:
           graph.close()

Identity, reconciliation and stale-input policy
----------------------------------------------------------------

Validity is exactly the single microsecond quantum ``[t, t+1 microsecond)``.
Keys, points and worlds have separate output identities. ``publication_key``
names a managed namespace; do not reuse it for unrelated jobs or edit its reserved
``gestaltdb:ontology`` claims manually. Mapping, source policy and ontology revision
can change within a namespace; the next call reconciles against the new closure.
Other agents/sources in the target world remain untouched.

The generation digest includes database identity, point/world/key, revision,
mapping, policy and the exact selected input version references. The receipt and
claim provenance also record the authenticated input context. Unchanged inputs
produce a no-op retry even though prior output commits advanced the database
horizon. After an interrupted call, rerun with the same parameters; visible
publication is recognized through canonical claim versions, not a separate
possibly missing dependency index. Failed inference publishes nothing.
Writes use the existing temporal commit visibility/recovery mechanism; see
:doc:`temporal-versioning` for orphan audit/reclamation. No new storage protocol
or power-loss durability guarantee is invented here.

Stale policy is **recompute latest while holding the temporal writer guard**.
This intentionally serializes writers during extraction and inference. Work
bounds limit kernel/adapter counts, not lock duration or backend authentication
cost. The input policy must exclude recognized derived claims. Full recomputation
from base evidence, not bounded witness removal, eliminates self-supporting
cycles and preserves independent support. Losing the final support retracts the
managed output; losing only one support updates its generation. This is not
incremental truth maintenance or an all-support proof index.

Interval semantics before any broader materialization
----------------------------------------------------------------

This API deliberately accepts no output interval or window. For a future interval
publisher, every rule application must intersect the validity sets of all its
premises, then union all independent supports of each conclusion. Maintain a
normalized, sorted, disjoint union of half-open intervals; merge adjacent intervals
but never fill gaps with a min/max envelope. Iterate those interval sets to a
bounded fixed point, retain revision/system-horizon separation, and recompute
or maintain all supports when deleting. Point witnesses cannot establish these
unions. Such an interval publisher and incremental support index are not supplied
by this operation.
