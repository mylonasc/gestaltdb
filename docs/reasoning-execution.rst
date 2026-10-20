:orphan:

Relational execution contracts
==============================

For compact dictionary-backed relation containers and optional Arrow exchange,
see :doc:`reasoning-batches`. These do not add new evaluator semantics.

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
----------------------------------------

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

Capability boundary (RE-01)
----------------------------------------

The baseline register at ``planning/reasoning-engine/baseline-review.json`` audits
every GR-001–136 exactly once against ``onto-dev`` commit ``c71f043``. Statuses
are qualified by evidence and notes; "available" never means the wider workbook
acceptance criterion is certified beyond the named fragment.

============================= ============================= ==============================
Operation                     Existing indexed evaluator    New relational contract
============================= ============================= ==============================
Constants/repeated variables  Executable, binary entities   Represented at positive arities
Safe positive recursion       Bounded complete fixpoint     Validated rules, not execution
Selection/projection/joins    Python indexed joins          Logical atom/head structure
Union/distinct/difference     Python fact sets              Set-valued schema declaration
Body delta occurrences        Every occurrence dynamically  Original positions retained
Join choice                   Smallest current bound bucket Description, no cost estimates
Witnesses                     Bounded single witness        Existing Evidence stays separate
Context/dictionary identity   Adapter context checks        Full compilation compatibility key
Literals/negation/aggregates   Unsupported                   Rejected/not represented
SCC/parallel/vectorized/spill  Unsupported                   Future executor work
============================= ============================= ==============================

Reproducible reference budgets
----------------------------------------

Before choosing an optimized executor, RE-01 freezes engineering budgets in
``planning/reasoning-engine/performance-budgets.json``. These are reference-machine
acceptance gates, not universal library latency promises. Each case runs both
closure-only and bounded-witness modes in five fresh worker processes:

====================== ======= ================= ==================== =============
Case                   Size    Pipeline P95 cap  Cold worker P95 cap  Worker RSS cap
====================== ======= ================= ==================== =============
Interactive hierarchy  256     0.25s             2s                   256 MiB
Batch tree             1024    3s                5s                   512 MiB
Dense output           128     3s                5s                   512 MiB
Update reference       512     1s                3s                   256 MiB
====================== ======= ================= ==================== =============

Reproduce from the repository::

   uv run python planning/reasoning-engine/check_roadmap.py
   uv run python -m benchmarks.reasoning_contracts --output /tmp/opencode/reasoning-baseline.json

Use ``--repeats 1`` only for smoke checks; the report identifies insufficient
acceptance repeats. The harness exits unsuccessfully for missing metrics, wrong
answers, timeouts or exceeded budgets. CI unit tests check fixtures and budget
accounting, not host-dependent timing assertions. P95 is nearest-rank; the maximum
worker RSS must satisfy its cap. Native means the existing Python engine in the
inherited benchmark nomenclature, not C++ or Rust.

The update case inserts one link, then removes its final support, comparing exact
analytical answers at each step. It performs **full rematerialization**, not
incremental propagation, and performs no persistence/publication. Future
incremental implementations must report their maintenance mode separately.
The other cases reuse the existing analytical ontology workloads and staged
worker measurements, rather than introducing another solver/reference oracle.

Pipeline includes compilation, conversion, fixpoint and decoding; imports, fixture
construction and validation are separate. Cold worker time includes interpreter
startup/imports. RSS is the full process high-water mark, not a subtraction of
startup high-water. Reports include source/harness digests, Python/platform,
dependency/source footprint and explicit thread environment. No cold-cache storage
or zero-copy claim is implied. Source extraction, native operator/copy/spill
counters, installed-wheel footprint and persistent incremental/publication costs
remain later phase gates; see :doc:`ontology-benchmarks` for existing claim-extraction
and solver comparison evidence.

The committed ``benchmarks/results/reasoning-re01-baseline.json`` records 40
successful rows (clean source ``99be3cb``, Python 3.13). Pipeline P95s for
closure/witness modes were 4.45/12.39ms interactive, 90.23/177.10ms sparse batch,
559.84/677.52ms dense output and 6.49/12.69ms for the complete three-state
rematerialization update workflow. Worker RSS stayed below 57 MiB. These are
synthetic reference observations on the recorded host, not optimized-engine
speedups, production-scale guarantees or incremental update latency claims.
