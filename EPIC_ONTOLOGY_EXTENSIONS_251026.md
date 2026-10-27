# Ontology extensions: composable typed, temporal and structural reasoning

Review date: 2026-10-26. Parent epic:
[#391](https://github.com/mylonasc/gestaltdb/issues/391) (open). This file
records the proposal context plus the support/effort analysis performed
against `main` at `0.14.0rc1`. It complements, and does not replace,
`EPIC_REASONING_ENGINE.md`, whose RE-01…RE-27 index is closed and
CI-checked. Nothing here claims implementation; each enhancement is tracked
in its own issue and delivered independently.

## Shared context

The 0.14.0rc1 foundation provides ontology compilation, binary Datalog
reasoning, typed n-ary rules, bounded explanations (binary engine), scoped
stratified negation, aggregates (point evaluation), explicit negative
validation, incremental maintenance, temporal range closure (positive rules),
and explicit graph adapters (`kg.entails`, projection, generation admission).

The gaps in #391 concern combining these capabilities without custom
reasoning infrastructure: realistic domains (e.g. FINOS/ISDA CDM) need
classification, constraints, provenance and temporal lifecycle evaluated
together. CDM-specific translation and business rules stay example-level
(`example/finos-isda-cdm`); the core enhancements below are
domain-independent. The end-to-end CDM scenario belongs in the tutorial
track ([#273](https://github.com/mylonasc/gestaltdb/issues/273)), not core.

None of the five items is in the closed RE-01…RE-27 roadmap, and no open
tracking issue previously planned them. Open PropEnh-11/PropEnh-12 overlap
in name only (old wishlist tracker; stratified negation has since shipped).

## Enhancements and analysis verdicts

### 1. Proof explanations for typed reasoning — [#392](https://github.com/mylonasc/gestaltdb/issues/392) (High)

Gap confirmed: `GenericEngine.evaluate(..., collect_proofs=True)` raises
`ReasoningError` (`reasoning/typed.py`); `adapters/typed_execution.py`
provides no witnesses. Partial support exists (`NegationWitness` blockers,
binary witnesses, `graph.rules.explain`), so limits infrastructure and
precedent are available. Effort: medium. Phase premises/comparisons first,
aggregates later; tie explanation lifecycle to support retraction.

### 2. Temporal reasoning with stratified negation and aggregates — [#393](https://github.com/mylonasc/gestaltdb/issues/393) (High)

Gap confirmed: `reasoning/temporal_closure.py` fails negation, aggregates
and closed scopes closed. Effort: largest and highest-risk — interval truth
maintenance with non-monotonic rules (absence across whole segments,
retraction-induced segment splits, per-segment grouping). Recommendation:
split into negation-over-closed-predicates first; defer
aggregates-over-intervals to a minimal segment-aligned subset. This is the
one item that may exceed the current roadmap unless tightly scoped.

### 3. Declarative structural validation — [#394](https://github.com/mylonasc/gestaltdb/issues/394) (High)

Gap confirmed: `reasoning/validation.py` covers denials, disjointness,
irreflexivity and functional conflicts; Cypher adds `UNIQUE` / `IS NOT NULL`.
No cardinalities, datatypes or exactly-one-of groups. Effort: smallest — a
new bounded read-only checker, no engine or truth-maintenance changes;
extend the `reasoning/profiles.py` matrix. The explicit-completeness
design in the proposal already resolves the open/closed-world hazard.

### 4. Opt-in reasoning-aware graph queries — [#395](https://github.com/mylonasc/gestaltdb/issues/395) (Medium)

Gap confirmed: Cypher has no inferred-pattern support; `kg.entails`,
projection and generation admission exist but require hand coordination.
Effort: medium, design-sensitive. Prefer querying a precomputed closure
with selection plus generation pins (reuses
`adapters/graph_execution.py`); do not intrude into the Cypher planner.
Cost is a read-only Cypher surface, not engine work.

Implementation follow-through (branch `feature/395-reasoning-aware-queries`):
the existing `inferred_view` fact/path API now underpins an explicit selected
Cypher projection in `adapters/queries.py`. Exact generation pins, mandatory
mode, live-source freshness and active-snapshot authentication accompany the
read-only node/one-hop subset. See `docs/reasoning-queries.rst` and the task
checklist in `planning/reasoning-followthrough/reasoning-aware-queries.md`.
No implicit reasoning, physical-data writes or general multi-hop support.

### 5. Richer graph-to-fact mappings — [#396](https://github.com/mylonasc/gestaltdb/issues/396) (Medium)

Gap confirmed: `GraphMapping` covers labels, edge types and single-string
node properties only ("never coerced literals"); a `mapping_id` digest
already exists. Effort: medium and incrementally backward compatible (v2
digest alongside v1). Main cost is identity/collision spec work.

## Suggested implementation order

The epic proposes 392 → 394 → 396 → 393 → 395. The analysis recommends
cheap enablers first, since validation and mappings unblock the CDM fixture
later items need: #394 → #396 → #392 → #395 → #393 (negation subset;
aggregates deferred). Either order keeps items independent.

## Scope and non-goals (from #391)

No complete OWL 2 DL/RL compliance, no full SHACL, no automatic Rune/CDM
translation, no built-in ISDA qualification, no implicit per-query reasoning,
no asserted-data mutation, no unrestricted negation/recursive aggregates.

## Links

- Parent epic #391; children #392, #393, #394, #395, #396.
- Prior roadmap: `EPIC_REASONING_ENGINE.md` / #156 (RE-01…RE-27 delivered).
- Tutorial track: #273. Related open work: #194 (native extraction).
