:orphan:

Relational execution contracts
==============================

The additive ``gestaltdb.reasoning.relational`` module defines the
``gestaltdb-relational-v1`` structural IR. It is independent of storage and optional
dependencies. ``RelationSchema`` describes ordered, uniquely named, non-null entity
columns with set semantics. ``RelationalAtom`` and ``RelationalRule`` support
positive arities, constant relation names and range-restricted single heads.
Strings, including ``"?x"``, remain constants; use ``Variable`` for variables.

This is a compilation boundary, **not an n-ary execution API**. The existing
``DatalogEngine`` still consumes binary ``Program`` values. Literal columns,
negation, aggregates, physical operators and SCC scheduling are not introduced by
this contract. Unknown representations fail explicitly rather than approximate.

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
duplicates. Every occurrence can serve as a delta pivot; actual applicability and
join order depend on runtime bindings/bucket sizes. Unknown estimated/actual rows
and selected indexes are ``None``, never guessed costs. Dynamic telemetry and
SCC-aware execution are future work.

Identities and evidence
----------------------

Legacy ``Program.digest`` retains its existing ``gestaltdb-datalog-v1`` encoding.
The relational IR uses a separate versioned namespace; lowering does not mutate
or replace the original program. Schema/catalog ordering is canonical, while
column order, rule IDs, body order and variable names participate in identity.
Unused declared relations also participate. Digests express structure, not
logical-equivalence detection.

``CompilationIdentity`` binds a program digest, the complete existing
``InputContext``, dictionary generation, semantics profile and executor version.
Use ``require_compatible`` before reusing context-bound state. The schema version
is included in its digest. This is a compatibility check, not a capability token:
adapters still authenticate their actual pinned evidence and rule/source horizon.
The structural description accepts only the currently implemented binary profile
and indexed Python executor; future executors must declare their own support.

Logical facts are set-valued; existing ``Evidence.reference_id`` retains exact
input identities independently. Dictionary generations are not evidence IDs,
RocksDB sequence numbers are not authenticated application horizons, and a witness
trace is not an all-support maintenance index. No database extraction, snapshot
acquisition, publication or cache access occurs during structural inspection.
