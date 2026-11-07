# Temporal Reasoning For Library Users

Read this topic when answers depend on *when* facts hold, not just which facts hold. No backend or solver extra is required; inputs are explicit `TemporalFact` values, not database history.

The human learning path is `docs/reasoning-temporal.rst` (change points, segments, point/range parity, evidence), then `docs/reasoning-range-tutorial.rst` (scoped absence, updates, proof budgets, aggregation, segment-scoped equality). Exact interval algebra lives in `docs/reasoning-intervals.rst`; update/publication/conformance contracts in `docs/reasoning-ranges.rst` and `docs/reasoning-conformance.rst`. Storing history (`temporal` topic) never runs inference by itself. Authenticated finite-range history uses `docs/reasoning-range-admission.rst`; guarded publication uses `docs/reasoning-bound-range-publication.rst`.

## Rules

- Import from `gestaltdb.reasoning`: `Interval`, `IntervalSet`, `TemporalFact`, `evaluate_range`, `update_range`, `verify_closure`.
- Pair each `TypedFact` with an explicit non-empty `IntervalSet` of half-open `[start, end)` ranges. Gaps stay gaps; the result never fills the envelope between disjoint intervals.
- `evaluate_range(program, facts, limits=...)` splits every validity bound into elementary segments with uniform active inputs, evaluates each segment with the ordinary engine, and unions only segments where the conclusion follows.
- Overlapping independent supports union; conjunctive premises intersect. Point answers (`closure.at(point)`) agree with fresh point evaluation by construction.
- Scoped absence and nonrecursive aggregates evaluate per segment with per-interval evidence; empty groups infer no zero counts.
- `update_range` reuses unchanged segments and recomputes only newly affected combinations; removals are support-aware. `verify_closure` re-segments and reevaluates through its own scan path as a conformance gate, not source authentication.
- `max_segments`, `max_supports`, sweep limits, and per-segment engine limits fail closed; they are not wall-time or memory guarantees. Truncated proof storage leaves logical conclusions complete; inspect `proofs_truncated` and `missing_evidence()`.
- Equality representatives are rebuilt per segment under explicit scope prefixes; they never merge identifiers globally or rewrite inputs.
- Computation grants no publication authority. Range admission, session admission, and bound publication are separate explicit stages with their own source/freshness/target checks.

## Example

```python
from gestaltdb.reasoning import GenericProgram, GenericRule, Interval, IntervalSet
from gestaltdb.reasoning import ReasoningLimits, TemporalFact, TypedAtom, TypedFact
from gestaltdb.reasoning import evaluate_range, verify_closure
from gestaltdb.reasoning import GenericEngine
from gestaltdb.reasoning.model import Variable

x, y, z = Variable("x"), Variable("y"), Variable("z")
program = GenericProgram([
    GenericRule("direct", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
    GenericRule("extend", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])],
                [], TypedAtom("reach", [x, z])),
])
facts = [
    TemporalFact(TypedFact("edge", ["a", "b"]), IntervalSet([Interval(0, 10)])),
    TemporalFact(TypedFact("edge", ["b", "c"]), IntervalSet([Interval(5, 15)])),
]
closure = evaluate_range(program, facts, limits=ReasoningLimits())
goal = TypedFact("reach", ["a", "c"])
assert closure.answers(goal).intervals == (Interval(5, 10),)
assert set(closure.at(7)) == set(GenericEngine().evaluate(
    program, [item.fact for item in facts if 7 in item.valid],
    limits=ReasoningLimits()).facts)
assert not verify_closure(program, closure, limits=ReasoningLimits()).proofs_truncated
```
