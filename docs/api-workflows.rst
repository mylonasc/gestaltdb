Runnable API Workflows
=======================

These examples live in the library docstrings: use Python ``help()`` in an
installed environment or read them here. Each example includes its imports and
input construction; persistence examples use disposable directories and close
their handles. They are executed by ``tests/test_public_workflow_docstrings.py``
in fresh namespaces, with real backends when the declared extras are installed.

Start with the :doc:`architecture` component map to select an application path.
The :doc:`api` page contains runnable class/method examples for ``GraphDB``,
Arrow ingestion, historical read views, stored-rule maintenance, all three KV
backends, JSON serialization, ``SamplerSnapshot`` / ``SamplerEngine``, and SVG
visualization. Install the indicated backend/Arrow extra before running those
examples (:doc:`installation`).

The workflows below need no database except the explicitly marked adapters.
They illustrate the difference between an in-memory result, saved reasoning
state, and claims deliberately published to storage.

Compile an ontology and evaluate facts
--------------------------------------

Use this for rule-based classification from a shared semantic model.
Supported constructs and axiom origins are described in :doc:`ontology`.

.. autofunction:: gestaltdb.ontology.compiler.compile_ontology

Binary and typed inference
---------------------------

The binary engine can collect bounded witnesses. The typed engine adds n-ary
relations and strict literal filters. See :doc:`reasoning-typed` and
:doc:`reasoning-negation` for aggregation and closed-world absence semantics.

.. autoclass:: gestaltdb.reasoning.engine.DatalogEngine
   :members: evaluate

.. autoclass:: gestaltdb.reasoning.typed.GenericEngine
   :members: evaluate, evaluate_stratified

.. autofunction:: gestaltdb.reasoning.adapters.execution.execute_closure

Selective answers, support maintenance, and validation
------------------------------------------------------

Use a bound goal for a particular entity, carry supports with a maintained
closure, or report semantic conflicts without changing the facts. See
:doc:`reasoning-demand`, :doc:`reasoning-maintenance`, and
:doc:`reasoning-validation` for the corresponding contracts.

.. autofunction:: gestaltdb.reasoning.demand.answer

.. autofunction:: gestaltdb.reasoning.maintenance.rematerialize

.. autofunction:: gestaltdb.reasoning.validation.validate

Reason over validity intervals
--------------------------------

This example computes when conclusions hold. It is separate from storing
temporal history, as explained in :doc:`temporal-versioning` and
:doc:`reasoning-temporal`.

.. autofunction:: gestaltdb.reasoning.temporal_closure.evaluate_range

Connect a graph and resume saved state
---------------------------------------

These examples require the ``lmdb`` extra. Projection consumes explicit mappings;
session advancement consumes committed evidence changes. See :doc:`ontology-graph`
and :doc:`reasoning-sessions` for the larger workflows.

.. autoclass:: gestaltdb.reasoning.adapters.graph.PinnedGraphSource

.. autofunction:: gestaltdb.reasoning.adapters.sessions.start_session

Longer source-verified workflows
---------------------------------

Some setup is important enough to keep in one complete guide instead of
repeating a large example in every function's docstring:

* :doc:`reasoning-authenticated-sessions`: obtain application-held source/state
  pins, save, load, verify, and advance durable reasoning state.
* :doc:`reasoning-bound-range-publication`: stage and publish interval results
  with explicit source/target policies and history checks.
* :doc:`reasoning-authenticated-inferred`: project saved conclusions to a new
  sampler artifact, verify a reload, and sample derived relationships.
* :doc:`reasoning-native-claims`: construct a temporal view, extraction request,
  selection/mapping policies, and bounded native batches.
* :doc:`inference-audit`: connect explicitly tagged model-output assertions to
  historical rule explanations and export an audit report.

Those guides include runnable examples with dedicated regression coverage. Their
entry-point docstrings point to the required inputs and the relevant guide where
this pass touched them; the examples are not replaced by disabled doctests.
