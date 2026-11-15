# Datalog For Library Users

Read this topic when deriving new facts from explicit rules without opening a database. No backend, solver, or network extra is required.

The human learning path is `docs/reasoning-introduction.rst` (facts, rules, closure, witnesses), then `docs/reasoning-typed-tutorial.rst` (comparisons, recursion, scoped absence, aggregation, proofs). Exact contracts live in `docs/reasoning-typed.rst`, `docs/reasoning-negation.rst`, `docs/reasoning-proofs.rst`, `docs/reasoning-demand.rst`, `docs/reasoning-validation.rst`, and `docs/reasoning-equality.rst`. For database-backed inference, use the `ontology` topic and `docs/reasoning-workflows.rst`, not these standalone engines.

## Rules

- Import engines from `gestaltdb.reasoning`, not the package root.
- Binary engine: `DatalogEngine().evaluate(program, [Fact(s, p, o)], limits=ReasoningLimits())` over `Program([Rule(...)])` with `Atom`/`Variable`. Entity-only, positive, bounded; closure includes input facts.
- Typed engine: `GenericEngine().evaluate(program, [TypedFact(...)], limits=...)` over `GenericProgram([GenericRule(...)])` with `TypedAtom`/`Variable`. Strict `Literal` values; `Literal("integer", 18)` and `Literal("text", "18")` are distinct, with no implicit conversion.
- Keep one arity per predicate within a program; head variables must be bound by positive body atoms.
- `Comparison(">=", age, Literal("integer", 18))` filters already-bound literals; incomparable kinds filter the candidate out rather than ordering it.
- Scoped absence is opt-in: pass `negated=[...]` with `closed=[...]` and call `evaluate_stratified`. Undeclared negation, unbound negation variables, and negative cycles fail at construction. Absence is not a negative claim.
- Aggregation is one nonrecursive stratum after the positive fixpoint (`count`/`sum`/`min`/`max`); aggregate heads are sinks and empty groups infer no zero-count fact.
- Bounds fail closed: `ReasoningLimits` caps logical work, and `collect_proofs=True` retains one bounded witness per conclusion, not all supports. Proof truncation never truncates the closure itself; reevaluate after facts or scopes change.
- `MultiHeadRule` normalizes to deterministic single-head rules; `expand_predicate_variables` requires an explicit finite domain. No open-world predicate invention.
- These engines never read databases, authenticate sources, or publish claims. Stored temporal claim rules (`graph.rules`) and database adapters are separate services.

## Example

```python
from gestaltdb.reasoning import Comparison, DatalogEngine, Fact, Literal, ReasoningLimits
from gestaltdb.reasoning import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
from gestaltdb.reasoning.model import Atom, Program, Rule, Variable

x, y, z = Variable("x"), Variable("y"), Variable("z")
program = Program([
    Rule("direct", [Atom(x, "edge", y)], Atom(x, "reach", y)),
    Rule("step", [Atom(x, "reach", y), Atom(y, "edge", z)], Atom(x, "reach", z)),
])
result = DatalogEngine().evaluate(
    program, [Fact("a", "edge", "b"), Fact("b", "edge", "c")],
    limits=ReasoningLimits(),
)
assert Fact("a", "reach", "c") in result

person, age = Variable("person"), Variable("age")
typed = GenericProgram([GenericRule(
    "adult",
    [TypedAtom("age", [person, age])],
    [Comparison(">=", age, Literal("integer", 18))],
    TypedAtom("adult", [person]),
)])
typed_result = GenericEngine().evaluate(
    typed,
    [TypedFact("age", ["alice", Literal("integer", 30)]),
     TypedFact("age", ["bob", Literal("integer", 12)])],
    limits=ReasoningLimits(),
)
assert TypedFact("adult", ["alice"]) in typed_result
assert TypedFact("adult", ["bob"]) not in typed_result
```
