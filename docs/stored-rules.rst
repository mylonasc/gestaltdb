Stored rules and support maintenance
=====================================

Use ``graph.rules`` when rules should derive **persisted claims** from sourced
temporal evidence. Begin with :doc:`claims-introduction` for polarity and
four-valued status. A :term:`Horn rule` derives a head proposition when its body
premises match; head variables must be bound by positive body atoms.

.. list-table:: Stored rules versus standalone Datalog
   :header-rows: 1

   * - Choice
     - ``graph.rules``
     - Standalone evaluators
   * - Input
     - Selected temporal positive entity claims in one world
     - Explicit programs and caller-supplied facts
   * - Result
     - Versioned derived claims with rule/premise justifications
     - In-memory closure; database publication is a separate adapter operation
   * - Features
     - Safe Horn rules and declared scoped absence
     - Binary/typed APIs have their own literals, aggregates and proof contracts
   * - Start here
     - This guide and :ref:`temporal-stored-rules-reference`
     - :doc:`reasoning-introduction`, :doc:`reasoning-typed`

Do not assume that every standalone rule feature is accepted by the stored-rule
service, or that evaluating a standalone program changes database records.

Keep a conclusion until its final support disappears
-----------------------------------------------------

One displayed :term:`witness` is not necessarily every independent :term:`support`.
Two sources can justify the same conclusion. Removing the first changes its
justifications; only removing the final support retracts the conclusion. This
standalone example checks both transitions and an older explanation.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           first, second = [graph.epistemic.assert_claim(
               subject="alice", predicate="WORKS_FOR", object="acme",
               polarity="positive", agent=agent, source=agent + ".csv",
               world="reported", valid=(0, 100),
           ) for agent in ("hr", "registry")]
           graph.rules.create_rule(
               "employment-affiliation", when=[("?p", "WORKS_FOR", "?c")],
               then=("?p", "AFFILIATED_WITH", "?c"),
           )
           limits = dict(max_iterations=20, max_derivations=100, max_justifications=1000)
           derived = graph.rules.run(as_of=50, world="reported", **limits).versions[0]
           graph.epistemic.retract_claim(first.logical_id, supersedes_version_id=first.version_id)
           changed = graph.rules.maintain(as_of=50, world="reported", **limits)
           assert changed.corrected_count == 1 and changed.retracted_count == 0
           assert graph.epistemic.get_claim_as_of(derived.logical_id, valid_time=50) is not None
           graph.epistemic.retract_claim(second.logical_id, supersedes_version_id=second.version_id)
           removed = graph.rules.maintain(as_of=50, world="reported", **limits)
           assert removed.retracted_count == 1
           assert graph.epistemic.get_claim_as_of(derived.logical_id, valid_time=50) is None
           historical = graph.rules.explain(
               derived.logical_id, valid_time=50, system_time=derived.system_time,
               max_depth=8, max_nodes=100,
           )
           assert {first.version_id, second.version_id} <= {
               node.node_id for node in historical.nodes
           }
       finally:
           graph.close()

Rules intersect premise validity within a justification and preserve independent
supports. Derived claims use the reserved ``gestaltdb:rules`` agent; they are not
authoritative base seeds that can keep an unsupported cycle alive. Reusing a rule
name appends a rule version. Call maintenance after changing premises or rules;
an unchanged repeat is idempotent. Use explicit valid/system horizons when
comparing historical explanations.

Absence is not an explicit denial
----------------------------------

``not_when=[...]`` checks that no matching positive premise is present under a
declared :term:`closed-world scope` (``closed_world`` predicates). It does not consume an explicit
negative claim, infer one, or make every missing predicate false. Negated variables
must be safely bound; undeclared negation and negative dependency cycles reject
before the catalog changes.

Stored-rule absence is conservative over a candidate conclusion interval: any
overlap with a disqualifying positive claim blocks the derivation. It does not
split out a surviving subinterval automatically. See the complete scoped-absence
example in :ref:`temporal-stored-negation-reference` for exact behavior; do not
transfer standalone range-negation contracts to this service.

Bounds, persistence and explanations
-------------------------------------

Set ``max_iterations``, ``max_derivations`` and ``max_justifications`` for the
workload. ``RuleEvaluationLimitError`` publishes no partial staged conclusions.
Successful conclusions publish in one temporal commit; backend transaction and
marker-last guarantees remain distinct (:doc:`temporal-updates`). Maintenance
recomputes a bounded closure and persists changes; it is not a promise of
deletion-local latency or constant memory.

``explain`` returns exact historical claim/rule versions and premise/absence
edges. ``max_depth``/``max_nodes`` bound explanation traversal; inspect
``truncated`` before presenting it as complete. A historical evidence graph is
not independent proof verification. :doc:`inference-audit` adds explicitly tagged
model outputs and bounded export without certifying model predictions or lineage.
