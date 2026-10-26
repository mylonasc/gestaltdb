Explicit positive typed application execution
============================================================

``reasoning.adapters.execution.execute_typed_closure`` connects the existing
``GenericEngine`` to explicit executor selection, generation admission and the
same ``StageTelemetry`` schema used by binary application paths. It does not
replace the binary dispatcher or introduce a new reasoning algorithm.

.. code-block:: python

   from gestaltdb.reasoning.model import Variable, ReasoningLimits
   from gestaltdb.reasoning.terms import Literal
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact, Comparison
   from gestaltdb.reasoning.adapters.execution import execute_typed_closure

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([GenericRule("adult", [TypedAtom("age", [x, y])],
       [Comparison(">=", y, Literal("integer", 18))], TypedAtom("adult", [x]))])
   outcome = execute_typed_closure(program,
       [TypedFact("age", ["alice", Literal("integer", 30)])], limits=ReasoningLimits())
   assert TypedFact("adult", ["alice"]) in outcome.result.facts
   assert outcome.telemetry.executor == "typed-indexed-python-v1"

Capabilities and conversion ownership
------------------------------------------

The default request for this **typed entry point** explicitly names
``positive-typed-point-v1``. ``select_executor`` selects the distinct
``typed-indexed-python-v1`` kind for this profile, with ``default`` or
``indexed-python`` preference and closure mode only. Ordinary
``ExecutorRequest()`` and the binary ``execute_closure`` default remain unchanged.
Neither entry point infers a representation from input values or silently calls
the other one. Literal types and n-ary tuples remain ``TypedFact`` values;
there is no binary stringification, compact dictionary adoption or recoding.

.. list-table:: Explicit dispatch capability boundary
   :header-rows: 1

   * - Request / program
     - Admitted path
   * - Binary positive closure / bounded witnesses
     - Existing binary dispatcher; existing reference/SCC/explicit Polars restrictions
   * - Positive typed rules, n-ary relations, strict literals and filters
     - ``execute_typed_closure`` / existing GenericEngine, closure only
   * - Typed proofs, prepared SCC or Polars preference
     - Reject before input; never weaken witnesses or recode
   * - Typed negation, aggregate or closed-scope program
     - Reject before input; separate explicit stratified/aggregate APIs remain available
   * - Maintenance supports or finite-range closure
     - Existing dedicated session/maintenance/range paths; not this dispatcher

Optional raw typed generation admission requires ``generation``,
``admission_request`` and independently supplied ``context`` together. Before
input iteration, the dispatcher checks the actual program/context/profile/
representation, selected executor and closure mode. The representation is
exactly ``typed-values-v1``. Shared ``admit`` has an explicit typed point closure row;
it rejects other modes, profiles, current/range sources and representations for the typed executor.
Legacy binary compatibility rows are unchanged. Raw typed facts and caller
identity strings are **not** independently authenticated by this API.

Authenticated session input
------------------------------------------

``reasoning.adapters.typed_execution.execute_authenticated_session`` connects
the source-verified typed-point session contract to actual fresh execution:

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning.model import Variable, ReasoningLimits
   from gestaltdb.reasoning.terms import Literal
   from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact
   from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
   from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
   from gestaltdb.reasoning.adapters.session_generation import bind_session_generation, catalog_for_typed_program
   from gestaltdb.reasoning.adapters.typed_execution import execute_authenticated_session

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([GenericRule("tag", [TypedAtom("memberOf", [x, y])], [],
       TypedAtom("tagged", [x, y, Literal("integer", 1)]))])
   with TemporaryDirectory() as root:
       graph = GraphDB.create(Path(root) / "db", backend="lmdb", serializer="json")
       try:
           for agent in ("one", "two"):
               graph.epistemic.assert_claim(subject="a", predicate="memberOf", object="g", agent=agent,
                                  polarity="positive", valid=(0, 100))
           session = start_session(graph, program, catalog_for_typed_program(program),
               scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
           binding = bind_session_generation(graph, session)
           executed = execute_authenticated_session(graph, session,
               expected_generation=binding.identity, expected_session_digest=binding.session_digest,
               limits=ReasoningLimits())
           assert TypedFact("tagged", ["a", "g", Literal("integer", 1)]) in executed.outcome.result.facts
           assert len(executed.evidence) == 2
           assert executed.outcome.telemetry.input_facts == 2
           assert executed.outcome.telemetry.admission_digest == binding.identity.digest
       finally:
           graph.close()

Both exact caller pins are required. The adapter independently re-admits the
complete active assertion ledger, including every alternative version and its
mapped fact/logical claim ID. It then evaluates **only active base input**, not
the supplied session's derived closure, and compares complete fresh closure to
the supplied state before returning. An internally consistent forged extra fact
can pass source-only binding but fails this fresh-execution comparison.
This is same-engine recomputation, not an independent proof verifier; support
index correctness is not certified by matching closure facts.

``AuthenticatedTypedExecution.evidence`` retains exact independent active
``AssertionVersion`` values. The engine consumes their facts as a multiset and
computes set closure; common ``input_facts`` counts version rows, not unique
logical facts. No witnesses, all-support provenance or proof of a particular
rule application is returned. Retired versions do not enter the engine.

The existing source policy remains positive **entity** claims at a temporal
point. Typed literals/n-ary values may be produced by rules; this does not
silently admit literal-valued database claims or n-ary source storage. Raw typed
execution can consume literal/n-ary ``TypedFact`` input without database source
authentication, as in the first example.

Freshness, bounds and side effects
------------------------------------------

Default ``freshness="latest"`` rejects source movement. Explicit historical
mode authenticates and evaluates the exact old prefix, never relabelling it as
current. The temporal writer guard spans admission, execution and final
source/store/decoder checks. Cooperating temporal writers wait; reentrant source
movement or handle rebinding fails before return. This may be a long-held guard,
not a low-latency or continuing freshness lease. Hostile raw storage writers are
outside this coordination contract.

``SessionAdmissionLimits`` governs source verification and retained state;
``ReasoningLimits`` governs engine rules, input versions, candidates, iterations
and closure facts. The raw dispatcher buffers at most ``max_input_facts`` rows
and consumes one additional row only to detect overflow. Cancellation is checked
before/after iterator advancement and through existing engine/admission checks;
it cannot interrupt a blocked iterator, lock wait or backend/native operation.
Cancellation and bound errors return no successful partial outcome and poison
no reusable state. These counters are not hard RSS/deadline guarantees.

The adapter does not advance, persist, repair or publish a session and writes no
database claims. New source state needs explicit session advance/rebinding.
Existing durable-session and interval-publication APIs stay separate; no
automatic executor adoption, optional dependency or performance claim is added.
