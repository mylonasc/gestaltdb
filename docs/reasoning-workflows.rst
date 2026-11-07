Integrate reasoning with application state
===========================================

Use these adapters when an application needs reasoning over database records,
saved state or inferred artifacts. Start with :doc:`reasoning-introduction` and
:doc:`reasoning-typed-tutorial` for standalone rules, or :doc:`reasoning-temporal`
for interval-valued conclusions. The adapters do not replace ordinary graph
queries or the separate :doc:`stored-rules` service.

Choose the source, fact model and required answer before choosing an executor.
An adapter's guarantees apply only to its documented combination: a current
snapshot is not temporal history, a goal answer is not a complete closure, and
saved-state compatibility is not source authentication.

Choose the workflow
--------------------

.. list-table:: Begin with the application task, not the implementation name
   :header-rows: 1
   :widths: 30 40 30

   * - Task
     - Select
     - Start here
   * - Reason over mutable nodes, labels, edges or properties
     - Explicit mapping and one verifiable current snapshot; binary or typed projection
     - :doc:`ontology-graph`
   * - Compute complete current-graph closure with generation checks
     - Authenticated complete binary projection and explicit executor dispatch
     - :doc:`reasoning-current-admission`
   * - Answer a positive binary goal without computing unrelated closure
     - Indexed frontier reads under one current or temporal view
     - :doc:`reasoning-current-frontier`, :doc:`reasoning-frontier`
   * - Extract historical claims for a point computation
     - Ordinary pinned claim source, explicit world/selection/mapping and exact version evidence
     - :doc:`reasoning-extraction`
   * - Maintain or resume typed point state as claims change
     - Session scope, catalog, source horizon and application-held state pins
     - :doc:`reasoning-sessions`, :doc:`reasoning-authenticated-sessions`
   * - Compute all validity intervals from authenticated history
     - Finite-range source extraction and complete range recomputation
     - :doc:`reasoning-range-admission`
   * - Read inferred results without writing claims
     - Explicit logical query view or independently hydrated inferred sampler artifact
     - :doc:`reasoning-queries`, :doc:`reasoning-authenticated-inferred`
   * - Persist conclusions into a managed target world
     - Separate guarded point or interval publication
     - :doc:`ontology-publication`, :doc:`reasoning-bound-range-publication`

**Full closure or a goal?** Full closure includes all consequences within the
selected inputs/program. :term:`Frontier evaluation <frontier evaluation>` fetches
facts needed for a supported goal under a pinned database view. Standalone
:doc:`reasoning-demand` specializes supported goals over supplied facts. Neither
route makes a partial result admissible as complete closure or supports every
typed, negative, aggregate or equality combination.

**Fresh execution or saved state?** A fresh run computes from selected input.
A :term:`session` retains active evidence and maintenance state across updates.
Reusing it requires the exact supported source, program, catalog, scope and state
pins; equal output facts alone do not establish compatibility. For a finite
validity range, use the range adapters rather than substituting a point session.

Run the ordinary executor first
--------------------------------

An :term:`executor` applies the selected program to facts. Indexed Python is the
default; installing Polars or using a native claim source does not change it.
The following **standalone, base-install example** dispatches a binary rule and
retains a bounded witness. Its caller-owned facts are not authenticated database
input; a successful computation grants no publication authority.

.. code-block:: python

   from gestaltdb.reasoning import Atom, Fact, Program, ReasoningLimits, Rule, Variable
   from gestaltdb.reasoning.adapters.execution import execute_closure
   from gestaltdb.reasoning.executors import ExecutorRequest

   x, y = Variable("x"), Variable("y")
   program = Program([Rule("copy", [Atom(x, "memberOf", y)], Atom(x, "affiliatedWith", y))])
   outcome = execute_closure(
       program, [Fact("alice", "memberOf", "lab")],
       executor_request=ExecutorRequest(mode="proofs"),
       limits=ReasoningLimits(), collect_proofs=True,
   )
   conclusion = Fact("alice", "affiliatedWith", "lab")
   assert conclusion in outcome.result
   assert not outcome.result.explain(conclusion).truncated
   assert outcome.telemetry.executor == "indexed-python-v1"

Use :doc:`reasoning-executors` for the dispatch/cancellation matrix and
:doc:`reasoning-plans` for explicit reusable positive binary plans. Typed
application execution uses its distinct GenericEngine contract
(:doc:`reasoning-typed-execution`); it is not binary recoding. Logical-work bounds
are not hard RSS or wall-time limits. Cancellation is cooperative, native calls
are not interrupted mid-operation, and failed evaluation returns no successful
partial closure. Bounded witness truncation can leave the logical result complete.

Keep identity, authentication and writes separate
--------------------------------------------------

A :term:`generation` identifies a particular source/program/context/representation
combination. A digest or manually constructed ``InputContext`` checks identity,
not whether caller-supplied facts really came from that source.

- **Source authentication** verifies database/view/history and selected evidence
  under the adapter's contract. It is not user login, real-world truth checking
  or an independent proof certificate.
- **Admission** checks the requested source/state and execution compatibility.
  Some APIs recompute closure; others check only specified identities or evidence.
  Read each contract rather than treating every admitted value as a certified proof.
- **Freshness** checks whether the selected source still satisfies the requested
  latest/historical policy. A historical temporal prefix is not a current snapshot
  that can be reopened after its lifetime ends.
- **Publication** requires its own guarded source/freshness and target checks.
  Read-only evaluation, source verification and artifact loading do not authorize
  writing inferred claims. Keep application-held expected pins independent of an
  untrusted file's own checksum.

Source and answer adapters
---------------------------

These task guides define mappings, view lifetimes, evidence and backend limits.
Keep extraction inside the caller-owned view's lifetime. Current-graph snapshot
capabilities vary by backend/runtime; unsupported verifiable snapshots fail
without a fallback to unpinned reads. Relation expansion and inferred-query
views expose logical relations, not physical parallel-edge multiplicity or
automatic rewriting of ordinary Cypher/sampler calls.

.. toctree::
   :maxdepth: 1

   ontology-graph
   reasoning-extraction
   reasoning-current-admission
   reasoning-current-frontier
   reasoning-frontier
   reasoning-demand
   reasoning-integration
   reasoning-queries

Maintain and resume selected state
-----------------------------------

Maintenance supports track independent derivations separately from a bounded
first witness. Sessions add selected source-version transitions; authenticated
resume adds source verification and exact application-held state pins. The
following references explain the distinct layers, not interchangeable session
formats. In-memory ledgers and metadata catalogs do not authenticate a source
by themselves.

.. toctree::
   :maxdepth: 1

   reasoning-maintenance
   reasoning-sessions
   reasoning-authenticated-sessions
   reasoning-generation-identity
   reasoning-admission
   reasoning-catalog
   reasoning-evidence-ledger

Consume or publish conclusions
-------------------------------

Read-only query and sampler consumers do not publish. Point publication uses a
single microsecond quantum; a point witness cannot establish complete interval
validity. Staged publication moves computation outside the writer guard but
still checks source freshness and target state under that guard. Range admission
and bound range publication are separate, computation-owned workflows; raw
caller-derived range staging has a weaker contract.

.. toctree::
   :maxdepth: 1

   reasoning-authenticated-inferred
   reasoning-inferred-consumers
   ontology-publication
   reasoning-generations
   reasoning-range-admission
   reasoning-bound-range-publication

Execution and representation reference
---------------------------------------

Prepared plans require exact identities; compact IDs require their exact
dictionary/context. The relational IR is structural inspection, not the typed
n-ary executor. Arrow exchange does not supply native joins, source decoding,
proof certification or publication authority.

.. toctree::
   :maxdepth: 1

   reasoning-executors
   reasoning-plans
   reasoning-typed-execution
   reasoning-execution
   reasoning-batches

Explicit alternatives and evidence
-----------------------------------

Ordinary extraction remains the default. Native claim sources require an
explicit choice and supported plain-PyRex/Arrow capabilities; they are point-only,
not current-graph, transactional or range admission. Full-record and historical
locator scans preserve different physical work with the documented common
logical selection contract. Raw native bytes alone are not authenticated facts.

Polars is an optional, experimental closure-only executor, never a proof or
publication fallback. The separate experimental EL classifier supports only its
explicit profile. Scope/calculus policy objects evaluate nothing and do not
enable probabilistic reasoning. Clingo and OWL comparison tools are benchmark
or conformance dependencies, not application executors.

.. toctree::
   :maxdepth: 1

   reasoning-native-claims
   reasoning-native-locator-claims
   reasoning-polars
   reasoning-el
   reasoning-scopes

The following engineering references retain semantic boundaries, resource
accounting and qualified measurement evidence. Synthetic timings are not
current-wheel performance guarantees or automatic executor/source selection.
For application-level checks, use :doc:`reasoning-testing`.

.. toctree::
   :maxdepth: 1

   ontology-contract
   reasoning-scale
   reasoning-gates
   reasoning-temporal-sweep
   reasoning-frontier-benchmarks
