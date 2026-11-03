:orphan:

Relational execution contracts
==============================

``gestaltdb.reasoning.relational`` defines the storage-independent
``gestaltdb-relational-v1`` structural intermediate representation (IR).
``RelationSchema`` describes ordered, uniquely named, non-null entity columns
with set semantics. ``RelationalAtom`` and ``RelationalRule`` support positive
arities, constant relation names and range-restricted single heads. Strings,
including ``"?x"``, remain constants; use ``Variable`` for variables.

This IR is a compilation/inspection boundary, **not an n-ary execution API**.
``DatalogEngine`` consumes binary ``Program`` values. For executable n-ary rules
and literals, use the separate typed API (:doc:`reasoning-typed`). For opt-in
prepared SCC scheduling and telemetry, see :doc:`reasoning-plans`. Compact batch
containers and Arrow exchange are described in :doc:`reasoning-batches`.

Structural inspection
---------------------

.. code-block:: python

   from gestaltdb.reasoning import Atom, Program, Rule, Variable
   from gestaltdb.reasoning.protocols import InputContext
   from gestaltdb.reasoning.relational import (
       CompilationIdentity, RelationalProgram, explain_plan,
   )

   x, y = Variable("x"), Variable("y")
   program = Program([Rule("copy", [Atom(x, "parent", y)], Atom(x, "ancestor", y))])
   ir = RelationalProgram.from_program(program)
   # Supplied by an already validated adapter; strings alone authenticate nothing.
   context = InputContext("example-horizon", "default", "ontology-v1", "policy-v1", "mapping-v1")
   identity = CompilationIdentity(
       program.digest, context, "dictionary-generation-1",
       "positive-binary-entity-v1", "indexed-python-v1",
   )
   description = explain_plan(program, identity=identity)
   assert description["kind"] == "structural-only"
   assert description["rules"][0]["body"][0]["actual_rows"] is None
   assert description["relational_digest"] == ir.digest
   identity.require_compatible(identity)

``explain_plan`` returns detached JSON-safe presentation data, not executable or
persisted plan state. Positions refer to original body occurrences, including
duplicates. Every occurrence can serve as a delta pivot; applicability and join
order depend on runtime bindings and bucket sizes. Unknown estimated/actual rows
and selected indexes are ``None``, not guessed costs. This structural description
does not collect runtime telemetry or schedule SCCs.

Identities and evidence
------------------------

``Program.digest`` retains its ``gestaltdb-datalog-v1`` encoding. The relational
IR has a separate versioned namespace; lowering does not mutate the original
program. Schema/catalog ordering is canonical, while column order, rule IDs,
body order and variable names participate in identity. Unused declared relations
also participate. Digests express structure, not logical equivalence.

``CompilationIdentity`` binds the program digest, complete ``InputContext``,
dictionary generation, semantics profile and executor version. Use
``require_compatible`` before reusing context-bound state. The schema version is
included in the digest. Compatibility is not authentication: adapters still
verify their actual pinned evidence and source/rule horizon. Structural
descriptions accept only their supported binary profile and indexed Python
executor identity; other combinations fail explicitly.

Logical facts are set-valued; ``Evidence.reference_id`` retains exact input
identities independently. Dictionary generations are not evidence IDs, backend
sequence numbers are not authenticated application horizons, and witnesses are
not all-support maintenance indexes. Structural inspection performs no database
extraction, snapshot acquisition, publication or cache access.

Execution boundaries
----------------------

.. list-table:: Structural representation versus evaluation
   :header-rows: 1

   * - Operation
     - Binary indexed evaluator
     - Relational IR
   * - Constants and repeated variables
     - Executable binary entity rules
     - Represented at positive arities
   * - Safe positive recursion
     - Bounded fixpoint
     - Validated rules, not execution
   * - Joins and delta occurrences
     - Runtime indexed matching
     - Original logical positions retained
   * - Witnesses
     - Bounded single witnesses
     - Evidence identities remain separate
   * - Literals, negation and aggregates
     - Use the separate typed evaluator
     - Not represented by this entity-only IR
   * - SCC schedules and telemetry
     - Available through prepared plans
     - Not performed by structural inspection

For executor choice, cancellation and row budgets, see :doc:`reasoning-executors`.
Logical work limits do not guarantee wall time or RSS; use process isolation for
hard resource limits and measure application data rather than relying on
synthetic reference budgets.
