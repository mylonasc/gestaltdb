Build typed rules step by step
================================

Start with :doc:`reasoning-introduction` for facts, variables, rule bodies/heads
and fixpoints. Use the typed API when a relation needs strict data values or more
than two arguments. Every example here is independent and needs only the base
install: no backend, serializer, solver or database source is involved.

Typed facts use **predicate, argument-list** order. Entity identifiers remain
strings; :term:`literals <literal>` carry an explicit kind. :term:`Arity <arity>`
is the number of arguments, not the number of facts. Keep a predicate's arity
consistent within a program. ``Generic`` names this general fact model, not a
probabilistic or automatically optimized execution mode.

1. Bind data, then compare it
-----------------------------

An atom binds Alice's age to ``age``. The comparison checks that already-bound
literal; the rule derives a unary ``adult`` fact. It does not generate ages or
coerce the entity string ``"30"`` into an integer.

.. code-block:: python

   from gestaltdb.reasoning import (
       Comparison, GenericEngine, GenericProgram, GenericRule, Literal,
       ReasoningLimits, TypedAtom, TypedFact, Variable,
   )

   person, age = Variable("person"), Variable("age")
   program = GenericProgram([GenericRule(
       "adult", [TypedAtom("age", [person, age])],
       [Comparison(">=", age, Literal("integer", 18))], TypedAtom("adult", [person]),
   )])
   facts = [TypedFact("age", ["alice", Literal("integer", 30)]),
            TypedFact("age", ["bob", Literal("integer", 12)])]
   result = GenericEngine().evaluate(program, facts, limits=ReasoningLimits())
   assert TypedFact("adult", ["alice"]) in result
   assert TypedFact("adult", ["bob"]) not in result
   assert Literal("integer", 18) != Literal("text", "18")

Literal kinds are strict: Boolean ``True`` is not an integer literal and integer
``18`` is not float ``18.0``. Supported comparison operators require safely bound
operands. Incomparable kinds filter a candidate out rather than inventing an
ordering. See :doc:`reasoning-typed` for null, NaN and lexical conversion details.

2. Let conclusions feed recursion
----------------------------------

The direct rule copies each edge into reachability. The recursive rule extends
known reachability by another edge. Shared variables force the middle endpoints
to match. Set semantics stop repeated derivations from creating duplicate facts;
the finite positive fixpoint includes ``a -> c`` even though that input edge is
absent. This is logical reachability, not a shortest-path result or a physical edge.

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericEngine, GenericProgram, GenericRule, ReasoningLimits,
       TypedAtom, TypedFact, Variable,
   )

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = GenericProgram([
       GenericRule("direct", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
       GenericRule("extend", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])],
                   [], TypedAtom("reach", [x, z])),
   ])
   result = GenericEngine().evaluate(
       program, [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"])],
       limits=ReasoningLimits(max_iterations=20, max_facts=100, max_candidate_rows=1000),
   )
   assert {fact for fact in result.facts if fact.predicate == "reach"} == {
       TypedFact("reach", ["a", "b"]), TypedFact("reach", ["b", "c"]),
       TypedFact("reach", ["a", "c"]),
   }

Iteration/fact/candidate limits fail closed; they are not wall-time or RSS caps.
Head variables must be bound by positive body atoms, so rules cannot silently
invent an unbounded population of new entities.

3. Declare absence and retain its witness
-----------------------------------------

Declare ``banned`` complete **for this evaluation's selected inputs** before
testing its absence. Alice is eligible because she is a candidate and has no
matching ban in that scope. Bob is blocked by a positive ban, not by a negative
candidate claim. ``evaluate_stratified`` finishes dependencies before applying
absence; undeclared negation or negative cycles reject explicitly.

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericEngine, GenericProgram, GenericRule, ReasoningLimits,
       TypedAtom, TypedFact, Variable,
   )

   person = Variable("person")
   program = GenericProgram([GenericRule(
       "eligible", [TypedAtom("candidate", [person])], [], TypedAtom("eligible", [person]),
       negated=[TypedAtom("banned", [person])],
   )], closed=["banned"])
   facts = [TypedFact("candidate", ["alice"]), TypedFact("candidate", ["bob"]),
            TypedFact("banned", ["bob"])]
   run = GenericEngine().evaluate_stratified(
       program, facts, limits=ReasoningLimits(), collect_proofs=True,
   )
   conclusion = TypedFact("eligible", ["alice"])
   assert conclusion in run.result and TypedFact("eligible", ["bob"]) not in run.result
   explanation = run.proofs.explain(conclusion, max_nodes=10)
   assert not explanation.truncated
   witness = next(node for node in explanation.nodes if node.fact == conclusion)
   assert witness.absence[0].scope == ("banned",)
   assert run.proofs.is_current_for(program, facts)
   assert not run.proofs.is_current_for(program, [*facts, TypedFact("banned", ["alice"])])

A retained :term:`witness` is tied to the program and input horizon. Reevaluate
after facts or closed scopes change. Proof budgets can truncate retained evidence
without truncating closure; inspect the trace/explanation flags. Successful
absence proof records differ from ``collect_negation_trace`` why-not diagnostics,
which record present blockers (:doc:`reasoning-negation`). Neither authenticates
arbitrary caller facts or proves the closed-world declaration justified.

4. Aggregate a completed group
-------------------------------

An :term:`aggregate` combines completed logical rows by grouping keys. Here it
counts each team's distinct member facts. The result is an integer literal, not
an entity identifier. Duplicate copies of the same fact do not increase the count.

.. code-block:: python

   from gestaltdb.reasoning import (
       AggregateRule, Aggregation, GenericEngine, GenericProgram, Literal,
       ReasoningLimits, TypedAtom, TypedFact, Variable,
   )

   team, person, count = Variable("team"), Variable("person"), Variable("count")
   program = GenericProgram([], aggregates=[AggregateRule(
       "team-count", [TypedAtom("member", [team, person])], [team],
       [Aggregation("count", person, count)], TypedAtom("team_size", [team, count]),
   )])
   facts = [TypedFact("member", ["lab", "alice"]), TypedFact("member", ["lab", "bob"]),
            TypedFact("member", ["lab", "alice"]), TypedFact("member", ["audit", "carol"])]
   result = GenericEngine().evaluate(program, facts, limits=ReasoningLimits(), collect_proofs=True)
   conclusion = TypedFact("team_size", ["lab", Literal("integer", 2)])
   assert conclusion in result
   assert TypedFact("team_size", ["audit", Literal("integer", 1)]) in result
   assert not result.proofs.explain(conclusion, max_nodes=10).truncated

Only the supported nonrecursive aggregation stratum is available: aggregate heads
are sinks, aggregate bodies cannot read other aggregate heads, and head arguments
must be group keys, constants or aggregate targets. Empty groups do not infer a
zero-count fact. Numeric ``sum``/``min``/``max`` have strict kind/null rules;
aggregation is not recursive arithmetic or an unrestricted SQL aggregate engine.

Continue beyond the first rules
--------------------------------

- **Multi-head rules:** ``MultiHeadRule`` normalizes a finite head conjunction to
  deterministic single-head rules. This changes representation, not truth status.
- **Predicate variables:** ``expand_predicate_variables`` requires an explicit
  finite predicate domain. It does not invent arbitrary new predicate names.
  Both contracts and exact imports are in :doc:`reasoning-typed`.
- **Focused answers or updates:** :doc:`reasoning-demand` specializes supported
  goals; :doc:`reasoning-maintenance` maintains supported closures. Do not assume
  typed proofs automatically provide all-support or incremental proof maintenance.
- **Validation and equality:** :doc:`reasoning-validation` reports violations;
  :doc:`reasoning-equality` provides snapshot-local representatives, not physical
  graph merges. Ontology classification is separate (:doc:`ontology`).
- **Time and storage:** :doc:`reasoning-temporal` adds validity intervals.
  :doc:`stored-rules` operates on persisted sourced claims rather than these
  caller-owned in-memory inputs. Database extraction/admission/publication require
  explicit adapters and do not follow automatically from a successful closure.
