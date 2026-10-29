In what follows, there is a long analysis of an LLM agent, on what capabilities are already implemented, and what capabilities need to be implemented in order for the gestaltdb to support ontological reasoning.
This is to be considered in combination with the excel feature list.

# GestaltDB: Ontological Reasoning Engine — Architecture Assessment and Feature Roadmap

Research and source-code review · 20 October 2026 · Focus: RocksDB/PyRex, language-independent reasoning primitives, and native execution

### Tracker refresh — 29 October 2026

Reconciled with local revision `364be6b` and `gestaltdb_reasoning_feature_tracker.xlsx`. The original architectural recommendations remain proposals, not delivered native-runtime functionality. Feature IDs below refer to the workbook's 136-row register; the broader inventory tables also contain constructs grouped differently from that register.

**Current register totals:** 24 Available · 31 Partial · 77 Required · 4 Optional. `Available` describes existing behavior, not a performance certification; `Partial` means the full acceptance criterion is not met. Owners remain unassigned. Workbook milestones are proposed delivery targets, not commitments.

| Feature ID | Construct | Previous → current | Evidence and remaining boundary |
| --- | --- | --- | --- |
| GR-011 | Canonical fact identity | Partial → Available | `epistemic.claim_statement_id` identifies propositions independently of sourced claim IDs. Compact native fact storage remains GR-009/GR-012. |
| GR-076 | Premise→conclusion dependencies | Partial → Available | `GraphDB.maintain_truth` persists `premise_dependents` in maintenance metadata and rebuilds it on retry. Not an index-driven deletion executor. |
| GR-077 | Rule→conclusion dependencies | Partial → Available | `GraphDB.maintain_truth` persists `rule_dependents`; rule-replacement tests cover the mapping. Not localized stratum invalidation. |
| GR-078 | Support counts | Required → Partial | Per-conclusion `support_count` is persisted alongside exact justifications. Counts are recomputed; counting-based incremental maintenance is not implemented. |
| GR-128 | Soundness differential tests | Required → Partial | A maintenance regression compares cycle removal against fresh recomputation. No broad independent reference/randomized differential corpus yet. |
| GR-130 | Deterministic repeated-run tests | Required → Partial | No-op reruns and interrupted-publication retries are tested. A general deterministic closure/provenance suite remains outstanding; new version IDs are not deterministic. |
| GR-136 | Recovery/checkpoint | Required → Partial | Interrupted dependency-metadata publication resumes idempotently. Long-running evaluation checkpoint/resume remains unimplemented. |

**Validation:** `uv run pytest tests/test_rules.py tests/test_truth_maintenance.py tests/test_epistemic.py tests/test_modal.py tests/test_temporal_epistemic_workflow.py -q` — **92 passed**. This is focused functional coverage, not a full-suite run, native/Python comparison, standards conformance result, or performance measurement.

**Milestone status:** M0 is Partial: immutable validated `RuleAtom`/`RuleVersion` values and functional regressions exist, but compiled relational IR, predicate dependency/SCC planning, reasoning-plan inspection, and a dedicated inference benchmark suite are still missing. M1–M7 remain Proposed. The workbook now includes per-feature evidence/boundary notes and target milestones without assigning owners.

**Scope guardrails:** rule evaluation still consumes only positive entity-object claims, requires constant predicates, joins in Python, and evaluates one `as_of` instant. Four-valued claim status does not provide negative rule premises or negation-as-failure. Confidence metadata does not implement probabilistic propagation. Existing point-selected interval union/intersection does not establish gap-preserving full-history closure. CDM schema/demo notebooks do not constitute an RDF/OWL ontology compiler or standardized entailment profile. Native scans/joins, DRed, atomic materialization-generation switching, and full-range temporal inference remain backlog items.

## 1. Executive assessment

GestaltDB already has a meaningful foundation for ontological reasoning. Its next major opportunity is not primarily a richer query language; it is a more capable, high-performance reasoning execution engine.

My recommendation is to evolve GestaltDB into a hybrid graph, relational, and deductive engine, retaining RocksDB as the persistent storage layer while introducing specialized execution structures for reasoning.

The architecture should support three complementary workloads:

1. Deductive closure: Deriving facts from relationships, class hierarchies, property characteristics, and rules until a fixpoint is reached.
2. Incremental reasoning: Updating derived facts and their justifications efficiently when assertions, rules, or ontologies change.
3. Contextual reasoning: Evaluating facts and derivations within temporal intervals, possible worlds, provenance contexts, and potentially contradictory information.

These are independent of whether queries eventually arrive through Cypher, SPARQL, an API, or another frontend.

### Principal findings

The semantic foundation is stronger than it initially appears.

GestaltDB already implements positive Horn rules, semi-naive fixpoint evaluation, rule versioning, derivation justifications, truth maintenance, bitemporal assertions, four-valued claim semantics, and bounded modal evaluation.

The principal execution bottleneck is architectural.

The current rule evaluator operates primarily over Python dictionaries, lists, sets, and nested matching loops. Its semi-naive algorithm is valuable, but it does not yet provide the compiled, indexed, vectorized execution model needed for large reasoning workloads.

RocksDB should remain the durable source of truth, not the only execution representation.

Dense integer IDs, specialized indexes, compact fact tables, delta relations, and native join operators can coexist with RocksDB, rather than replacing it.

Native code is justified, but a complete rewrite is not.

Move bounded, high-frequency operations—joins, unification, delta propagation, deduplication, and dependency processing—into Rust or C++. Keep semantic modeling, APIs, rule management, and orchestration in Python initially.

The central design principle is:

Make reasoning a compiled data-processing problem rather than a Python-level process of repeatedly interpreting individual graph objects.

## 2. What GestaltDB already provides

This assessment uses the public `main` branch source, the current documentation, and the PyRex backend documentation. It distinguishes declared capabilities from the internal implementation and avoids treating a feature as optimized merely because an API exists.

Principal inspected sources: [graphdb.py](https://raw.githubusercontent.com/mylonasc/gestaltdb/main/src/gestaltdb/graphdb.py?utm_source=chatgpt.com), [rules.py](https://raw.githubusercontent.com/mylonasc/gestaltdb/main/src/gestaltdb/rules.py?utm_source=chatgpt.com), [kvstores.py](https://raw.githubusercontent.com/mylonasc/gestaltdb/main/src/gestaltdb/kvstores.py?utm_source=chatgpt.com), and [temporal reasoning documentation](https://mylonasc.github.io/gestaltdb/temporal-versioning.html?utm_source=chatgpt.com).&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+3



### Verified capabilities

| Capability                                   | Current state                    | Reasoning significance        |
| -------------------------------------------- | -------------------------------- | ----------------------------- |
| Attributed nodes and typed edges             | Implemented                      | Basic graph representation    |
| Label, property, composite and range indexes | Implemented                      | Candidate filtering           |
| Directed typed adjacency                     | Implemented                      | Relation traversal            |
| Append-only temporal versions                | Implemented                      | Historical reasoning          |
| Bitemporal point-in-time reads               | Implemented                      | Temporal context              |
| Consistent temporal read views               | Implemented                      | Stable evaluation horizon     |
| Positive/negative sourced claims             | Implemented                      | Explicit evidence             |
| Four-valued claim status                     | Implemented                      | Contradiction tolerance       |
| Positive Horn rules                          | Implemented, restricted          | Deductive inference           |
| Semi-naive recursive evaluation              | Implemented in Python            | Fixpoint computation          |
| Validity-interval intersection               | Implemented                      | Temporal derivation           |
| Rule versions                                | Implemented                      | Ontology evolution            |
| Derived-claim justifications                 | Implemented                      | Inference provenance          |
| Truth maintenance                            | Implemented, recomputation-based | Retraction and correction     |
| Claim explanation graphs                     | Implemented                      | Explainability                |
| Bounded modal evaluation                     | Implemented                      | World-relative reasoning      |
| Arrow/Polars bulk ingestion                  | Implemented                      | Efficient data loading        |
| Native RocksDB columnar writes               | Implemented through PyRex        | Reduced Python write overhead |

There are important qualifications.

Rules are currently restricted to positive subject–predicate–object atoms, with constant predicates and entity-valued objects. They do not yet constitute a general relational reasoning algebra supporting arbitrary arity, comparisons, typed literals, aggregation, existential witnesses, or stratified negation.

Semi-naive evaluation is present, but its execution remains Python-oriented. The current evaluator groups facts by predicate and tracks deltas, then repeatedly unifies variable bindings through nested loops. That is not equivalent to an index-driven or compiled relational join executor.

Truth maintenance is semantically more developed than its incremental execution mechanism. The current implementation can preserve multiple independent justifications and retract conclusions without surviving support, but reconciliation recomputes a bounded fixpoint from authoritative base claims. A truly differential, change-propagating engine is a separate optimization.

Finally, the RocksDB columnar ingestion optimization should not be mistaken for zero-copy reasoning. Efficiently writing Arrow-backed input is only one part of the pipeline; reasoning also needs efficient indexed reads, intermediate relations, joins, and result publication.

These distinctions drive the roadmap below.

## 3. Comprehensive feature inventory

I would organize the feature register into ten functional areas. The tables below are designed as the basis of a development tracker.

Status legend: `Available` means a capability is evidenced in the implementation. `Partial` means supporting machinery exists but not the complete general-purpose construct. `Required` means I did not establish a dedicated implementation in the inspected source and recommend adding it. `Optional` means it belongs to a more specialized or advanced reasoning profile.

Priorities are P0 (foundation), P1 (essential reasoning capability), P2 (advanced capability), and P3 (specialized/research).

### A. Fact representation, identity, and storage

| Feature / construct                                 | Status    | Priority |
| --------------------------------------------------- | --------- | -------- |
| Stable entity and relationship identifiers          | Available | P0       |
| Typed binary relationships                          | Available | P0       |
| Subject–predicate–object fact representation        | Available | P0       |
| Explicit positive and negative facts                | Available | P0       |
| Source, agent, world, and confidence metadata       | Available | P0       |
| Bitemporal immutable fact versions                  | Available | P0       |
| Arbitrary-arity internal relations                  | Required  | P1       |
| Typed literal terms participating in rules          | Partial   | P1       |
| Compact integer dictionary for symbols and entities | Required  | P0       |
| Separate base and derived fact relations            | Partial   | P0       |
| Canonical fact identity independent of evidence     | Available | P0       |
| Deduplicated set-based fact storage                 | Partial   | P0       |
| Explicit equality/equivalence relation              | Required  | P1       |
| Canonical equivalence-class representatives         | Required  | P1       |
| Named graph/context partitioning in execution       | Partial   | P1       |
| Compressed columnar fact segments                   | Required  | P1       |

The highest-leverage storage change is the compact symbol dictionary.

Instead of carrying Python strings through every evaluation step, assign integers to entities, predicates, classes, worlds, and relevant literal values.

For example, the proposition `alice WORKS_FOR acme` can become three integer IDs. An internal fact might be:

```
struct Fact {
    uint64_t subject;
    uint32_t predicate;
    uint64_t object;
    uint32_t context;
};
```

This is illustrative, not a proposed final ABI. Time intervals, polarity, provenance, and version identities should be represented in associated typed columns rather than necessarily enlarging every hot-path tuple.

Compact IDs enable faster equality comparisons, hashing, sorting, vectorization, bitmap indexing, and memory-efficient working sets.

They also provide a clean separation between logical fact identity and the evidence supporting that fact. Multiple independently sourced assertions must not be collapsed into one version identity.

### B. Core relational execution operators

These are arguably the most important missing general-purpose constructs.

| Feature / construct                         | Status                        | Priority |
| ------------------------------------------- | ----------------------------- | -------- |
| Indexed relation scan                       | Partial                       | P0       |
| Constant and variable binding               | Available                     | P0       |
| Equality-based unification                  | Available, Python             | P0       |
| Selection/filter                            | Partial                       | P0       |
| Projection                                  | Available through query layer | P0       |
| Multi-relation equijoin                     | Partial, Python               | P0       |
| Hash join                                   | Required in reasoning engine  | P0       |
| Sort-merge join                             | Required                      | P1       |
| Index nested-loop join                      | Required                      | P0       |
| Semi-join                                   | Required                      | P1       |
| Anti-join                                   | Required                      | P1       |
| Relational union                            | Partial                       | P0       |
| Set difference and duplicate elimination    | Partial                       | P0       |
| Distinct/group-by/aggregation               | Partial                       | P1       |
| Arithmetic and comparison predicates        | Partial                       | P1       |
| Variable predicate-position matching        | Required                      | P1       |
| Cost-based join ordering                    | Required                      | P0       |
| Relation cardinality/selectivity statistics | Required                      | P0       |
| Batched vectorized execution                | Required                      | P0       |
| Worst-case-optimal multiway joins           | Optional                      | P2       |

A conventional ontology reasoner is effectively evaluating joins over facts and axioms.

Consider:

```
WORKS_FOR(person, company)
SUBSIDIARY_OF(company, parent)

=> WORKS_FOR(person, parent)
```

The computational core is a join on `company`, followed by projection and deduplication.

That same machinery implements large parts of subclass propagation, property-chain inference, type inheritance, and recursive derivation.

Why this matters: The current implementation iterates through Python fact dictionaries for each rule-body atom, matching variables row by row. Even when facts are grouped by predicate, the engine repeatedly examines candidates that could be excluded by an index before joining.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+1



A native join engine, sensible indexes, and cost-based planning will generally provide more value than dozens of individually optimized ontology operators.

### C. Deduction and fixpoint evaluation

| Feature / construct                               | Status                        | Priority |
| ------------------------------------------------- | ----------------------------- | -------- |
| Safe, range-restricted positive rules             | Available                     | P0       |
| Recursive rules                                   | Available                     | P0       |
| Fixpoint iteration                                | Available                     | P0       |
| Semi-naive delta evaluation                       | Available, Python             | P0       |
| Per-body-position delta joins                     | Required                      | P0       |
| Persistent `new`, `delta`, and `stable` relations | Required                      | P0       |
| Dependency graph between rules/predicates         | Partial                       | P0       |
| Strongly connected component analysis             | Required                      | P0       |
| Recursive stratum scheduling                      | Required                      | P0       |
| Rule compilation to an execution plan             | Required                      | P0       |
| Generalized substitutions and typed terms         | Partial                       | P1       |
| Multiple rule heads / normalized multi-head rules | Required                      | P2       |
| Stratified negation                               | Required                      | P1       |
| Safe aggregation inside rules                     | Required                      | P2       |
| Bounded existential witness generation            | Required for EL-style support | P2       |
| Backward/goal-directed reasoning                  | Required                      | P2       |
| Hybrid forward/backward execution                 | Required                      | P2       |
| Magic-set or demand-driven specialization         | Required                      | P2       |
| Incremental rule addition/removal                 | Partial                       | P1       |
| Parallel fixpoint evaluation                      | Required                      | P1       |
| Work-queue/event-driven inference                 | Required                      | P1       |

The current semi-naive evaluator checks whether a completed rule match used at least one delta fact. A more efficient implementation should generate plans where a designated body occurrence reads only the delta relation.

Conceptually:

\\[ \Delta H = (\Delta A\Join B) \\;\cup\\; (A\Join\Delta B) \\]

For two body atoms, both delta substitutions must be considered, with duplicate elimination. More sophisticated implementations avoid repeatedly producing the same combination.

This change is especially important for recursive transitive closure, equivalence propagation, and highly connected knowledge graphs.

### D. Ontology constructs and axiom compilation

I recommend a first-class ontology compiler that translates declarative axiom definitions into a small number of efficient inference operators.

It should not depend on any specific surface query language.

| Construct                                  | Status                     | Priority |
| ------------------------------------------ | -------------------------- | -------- |
| Class/type assertions                      | Representable              | P0       |
| Subclass hierarchy propagation             | Rule-expressible           | P1       |
| Subproperty hierarchy propagation          | Rule-expressible           | P1       |
| Transitive class/property closure          | Rule-expressible           | P1       |
| Domain/range type inference                | Rule-expressible           | P1       |
| Inverse properties                         | Rule-expressible           | P1       |
| Symmetric properties                       | Rule-expressible           | P1       |
| Transitive properties                      | Rule-expressible           | P1       |
| Reflexive/irreflexive properties           | Partial                    | P1       |
| Equivalent classes/properties              | Rule-expressible           | P1       |
| Property chains                            | Partially rule-expressible | P1       |
| Disjoint classes/properties                | Required                   | P1       |
| Identity equivalence (`sameAs`)            | Required                   | P1       |
| Explicit inequality (`differentFrom`)      | Required                   | P1       |
| Functional/inverse-functional properties   | Required                   | P1       |
| Class intersections                        | Partially rule-expressible | P1       |
| Class unions                               | Partially rule-expressible | P2       |
| Complement/negated class constraints       | Required                   | P2       |
| Existential restrictions                   | Required                   | P2       |
| Universal restrictions                     | Required                   | P2       |
| Value restrictions (`hasValue`, `hasSelf`) | Required                   | P2       |
| Cardinality restrictions                   | Required                   | P2       |
| Property keys / identity inference         | Required                   | P2       |
| Ontology/class satisfiability checks       | Required                   | P2       |
| Complete OWL 2 DL reasoning                | Optional                   | P3       |

Here, rule-expressible deliberately does not mean automatically supported as an ontology construct. It means a user can manually express at least the straightforward inference pattern in the current positive-rule subset.

For instance:

\\[ \operatorname{type}(x,A)\land \operatorname{subClassOf}(A,B) \Rightarrow \operatorname{type}(x,B) \\]

This requires constant predicates and subject/object variables, both of which GestaltDB already has.

But automatically interpreting imported ontology axioms, handling equality, checking inconsistencies, and guaranteeing completeness for a standardized profile require further machinery.

Recommended standards target: RDFS plus a deliberately selected OWL 2 RL subset first, then progressively complete OWL 2 RL.

OWL 2 RL is explicitly designed for rule-based reasoning. OWL 2 EL is a different target, particularly relevant for large classification ontologies, while OWL 2 QL emphasizes query rewriting rather than wholesale materialization. These distinctions come from the W3C specifications, not from surface syntax.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.w3.org\&sz=32)

W3C

+1



### E. Incremental maintenance and derivation provenance

| Feature / construct                             | Status                     | Priority |
| ----------------------------------------------- | -------------------------- | -------- |
| Immutable derivation justifications             | Available                  | P0       |
| Multiple independent supports                   | Available                  | P0       |
| Retraction after last support disappears        | Available                  | P0       |
| Historical explanation graphs                   | Available                  | P0       |
| Deterministic derivation provenance             | Available                  | P0       |
| Premise-to-conclusion reverse dependencies      | Available, metadata maps   | P0       |
| Rule-to-conclusion reverse dependencies         | Available, metadata maps   | P0       |
| Persistent per-support reference counts         | Partial, conclusion counts | P1       |
| Incremental insertion propagation               | Partial                    | P0       |
| Incremental deletion propagation                | Partial                    | P0       |
| Delete-and-rederive (DRed)                      | Required                   | P1       |
| Counting-based incremental maintenance          | Required                   | P1       |
| Differential updates with signed multiplicities | Required                   | P2       |
| Versioned/materialized inference generations    | Required                   | P1       |
| Local invalidation after rule changes           | Required                   | P1       |
| Recursive support-cycle handling                | Available in recomputation | P0       |
| Minimal explanations                            | Required                   | P2       |
| Top-k or cost-ranked explanations               | Optional                   | P3       |

The distinction between current and proposed maintenance is critical.

GestaltDB already reconciles changed derivations, but its `maintain_truth` implementation rebuilds the relevant fixpoint from authoritative base facts, then compares it with existing conclusions.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+1



For large databases, a more advanced implementation should respond to an update by propagating only the affected changes through indexed dependency relationships.

However, reference counts alone are not enough for deletion under recursive derivation. Cyclic conclusions can support one another after their original external support disappears. DRed, an equivalent support-sensitive algorithm, or an appropriately designed differential implementation is needed to avoid this.

### F. Truth semantics, constraints, and consistency

| Feature / construct                             | Status    | Priority |
| ----------------------------------------------- | --------- | -------- |
| Open-world `unknown` status                     | Available | P0       |
| Distinct positive and negative evidence         | Available | P0       |
| Four-valued contradiction handling              | Available | P0       |
| Boolean connectives in modal formulas           | Available | P0       |
| Consistent rule evaluation horizon              | Available | P0       |
| Rules consuming explicit negative premises      | Required  | P1       |
| Stratified negation-as-failure                  | Required  | P1       |
| Disjointness/violation derivation               | Required  | P1       |
| Type and range constraint checking              | Partial   | P1       |
| Equality/inequality contradiction detection     | Required  | P1       |
| Functional property violation detection         | Required  | P1       |
| Typed datatype validation                       | Required  | P1       |
| Constraint violation provenance                 | Required  | P1       |
| Configurable classical vs paraconsistent policy | Required  | P1       |
| Closed-world scopes for selected predicates     | Required  | P2       |
| SHACL-style validation operators                | Required  | P2       |
| Probabilistic or fuzzy inference                | Optional  | P3       |

An important semantic requirement: classical ontology inconsistency, paraconsistent contradictions, explicit denial, and negation-as-failure must remain distinct concepts.

GestaltDB's current four-valued model is attractive because conflicting sources do not automatically make all propositions true. But complete standards-oriented OWL reasoning and paraconsistent inference do not automatically have identical entailment semantics.

I recommend an explicit semantics profile selected during reasoning-plan compilation, with independently testable guarantees. Avoid a generic `NOT` operator whose meaning changes silently depending on context.

### G. Temporal, contextual, and epistemic reasoning

| Feature / construct                               | Status    | Priority |
| ------------------------------------------------- | --------- | -------- |
| Valid-time intervals                              | Available | P0       |
| System-time version horizons                      | Available | P0       |
| Temporal retractions and corrections              | Available | P0       |
| Temporal premise intersection                     | Available | P0       |
| Consistent read-view provenance                   | Available | P0       |
| Scoped possible worlds                            | Available | P0       |
| Explicit accessibility relations                  | Available | P0       |
| Bounded belief/knowledge/possibility evaluation   | Available | P0       |
| Temporal delta relations                          | Required  | P1       |
| Disjoint interval-set representation              | Required  | P1       |
| Interval normalization/coalescing                 | Partial   | P1       |
| Interval joins and range indexes in native engine | Partial   | P1       |
| Reasoning across complete time ranges             | Required  | P1       |
| Context-aware deduplication and joins             | Partial   | P1       |
| Cross-context inference policies                  | Required  | P2       |
| Context hierarchy/inheritance                     | Required  | P2       |
| Confidence propagation policies                   | Partial   | P2       |
| Temporal modal closure                            | Required  | P2       |
| Change-point-driven incremental inference         | Required  | P2       |

GestaltDB's existing valid-time reasoning has an interesting limitation: rule execution is evaluated at a selected `as_of` instant. It intersects the valid intervals of premises supporting the selected derivations.

That is useful and coherent for point-in-time evaluation. It is not yet the same as computing the complete derived history over every interval in which a conclusion is true.

For full temporal ontological reasoning, derived validity should be represented as a possibly disjoint set of half-open intervals, retaining separate supports for each segment.

For example, if evidence supports a relationship in January and again in March, the engine must not silently infer that it held during February.

### H. Optimization, indexing, and execution strategy

| Feature / construct                         | Status    | Priority |
| ------------------------------------------- | --------- | -------- |
| Point lookup and sorted prefix scan         | Available | P0       |
| Typed forward/reverse adjacency indexes     | Available | P0       |
| Temporal lookup/index pruning               | Available | P0       |
| Prefix-oriented relation indexes            | Partial   | P0       |
| Predicate/subject/object permutations       | Required  | P0       |
| Join-key-specific indexes                   | Required  | P0       |
| Multi-column selectivity statistics         | Required  | P1       |
| Adaptive hash/sort/merge join selection     | Required  | P1       |
| Materialized schema closure                 | Required  | P1       |
| Memoization of repeated subgoals            | Required  | P1       |
| Bitmap indexes and set intersections        | Required  | P1       |
| Specialized transitive-closure operator     | Required  | P1       |
| Specialized equivalence/union-find operator | Required  | P1       |
| Multiway join planning                      | Required  | P2       |
| Goal-directed query rewriting               | Required  | P2       |
| Incremental view caching                    | Required  | P2       |
| Adaptive spilling to RocksDB/disk           | Required  | P1       |
| Native execution profiling                  | Required  | P0       |

The most appropriate indexes depend on the binding pattern.

For ternary facts `(subject, predicate, object)`, indexes such as `PSO`, `POS`, `SPO`, and `OPS` cover different operations. Not every possible permutation must be materialized from day one; choose them using workload statistics and scan-frequency measurements.

The existing key-value index layer should be reused where possible, but predicate-first reasoning indexes must be designed around the fact access patterns rather than only the existing graph traversal patterns.

### I. Native interoperability, persistence, and concurrency

| Feature / construct                         | Status                        | Priority |
| ------------------------------------------- | ----------------------------- | -------- |
| RocksDB native batch writes                 | Available                     | P0       |
| Arrow-backed payload ingestion              | Available                     | P0       |
| Polars-based batch payload construction     | Available                     | P0       |
| Consistent immutable temporal publication   | Available                     | P0       |
| Native batched indexed fact scans           | Required                      | P0       |
| Batched multi-key reads                     | Required                      | P0       |
| Arrow C Data/C Stream support               | Required in reasoning runtime | P0       |
| Native encoded-term dictionary              | Required                      | P0       |
| Compact columnar intermediate results       | Required                      | P0       |
| Native multi-threaded joins                 | Required                      | P1       |
| Native delta/work-queue processing          | Required                      | P1       |
| Batched derivation publication              | Partial                       | P0       |
| Atomic materialization generation switching | Required                      | P1       |
| Incremental compaction of derived indexes   | Required                      | P2       |
| Memory budgets and spill management         | Required                      | P1       |
| Cooperative cancellation                    | Required                      | P1       |
| GIL-free long-running native execution      | Required                      | P1       |
| Native telemetry and operator-level metrics | Required                      | P0       |

### J. Correctness, observability, and interoperability

| Feature / construct                              | Status                       | Priority |
| ------------------------------------------------ | ---------------------------- | -------- |
| Resource-bounded inference                       | Available                    | P0       |
| Rule and explanation version identities          | Available                    | P0       |
| Temporal integrity/provenance checks             | Available                    | P0       |
| Reasoning-plan inspection (`explain_plan`)       | Required                     | P0       |
| Derived-result soundness tests                   | Partial, recomputation check | P0       |
| Closure/completeness tests for declared profile  | Required                     | P0       |
| Deterministic execution regression suite         | Partial, retry/no-op checks  | P0       |
| Native-versus-Python differential tests          | Required                     | P0       |
| RDF/OWL ontology import and normalization        | Required                     | P1       |
| Standard RDF entailment test coverage            | Required                     | P1       |
| Full-load/reasoning/update benchmarks            | Required as a distinct suite | P0       |
| Per-operator time, rows, allocations, and memory | Required                     | P0       |
| Snapshot/ontology/ruleset compatibility checks   | Partial                      | P1       |
| Safe timeout and memory-exhaustion reporting     | Partial                      | P0       |
| Checkpoint/restart of long inference             | Partial, metadata retry only | P2       |
| Reproducible performance regression tracking     | Required                     | P1       |

These tables represent the recommended target capability inventory, not a claim that every row is necessary for every ontology profile. In particular, general Horn materialization, OWL 2 RL, OWL 2 EL classification, and modal reasoning have different completeness and performance requirements.

### Downloadable feature tracker

I've also organized the audit into an editable Excel workbook, so this does not have to remain a static report.

GestaltDB Ontological Reasoning Feature Tracker

136 tracked constructs · status and priority dashboards · editable owners, milestones and acceptance criteria

# 24

Available

# 31

Partial

# 77

Required

# 4

Optional

These are the refreshed 136-row workbook classifications as of 29 October 2026, not automated test verdicts. The focused 92-test result and precise status changes are recorded above. The workbook's dashboard recalculates as statuses are changed.

## 4. What established reasoning engines teach us

I would use six systems as architectural references. None should be copied wholesale; each demonstrates a particular technique worth separating from its language or storage format.

| System                | Relevant implementation techniques                                                           | What GestaltDB should learn                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Soufflé               | Compiled execution, automatic index selection, B-tree/Brie/equivalence relations, magic sets | Compile rule bodies into optimized indexed joins instead of interpreting substitutions       |
| RDFox                 | Parallel materialization, incremental maintenance, equality reasoning                        | Incremental derivation and deletion deserve their own execution engine                       |
| VLog / Nemo           | Vertical/columnar storage, compressed relations, existential-rule reasoning                  | Specialized relation layouts can substantially reduce memory and intermediate results        |
| ELK                   | Consequence-based classification, concurrency, incremental ontology updates                  | Large ontology classification benefits from specialized completion algorithms                |
| Apache Jena           | RETE-based forward reasoning, tabled backward reasoning, hybrid inference                    | Total closure should not be the only execution strategy                                      |
| Differential Dataflow | Stateful indexed arrangements, signed updates, iterative differential computation            | A powerful route to efficient recurring updates, but with substantial integration complexity |

Sources: Soufflé's [relation/index designs](https://souffle-lang.github.io/relations?utm_source=chatgpt.com), RDFox's [reasoning architecture](https://www.cs.ox.ac.uk/isg/tools/RDFox/?utm_source=chatgpt.com), VLog's [vertical execution approach](https://iccl.inf.tu-dresden.de/web/VLog?utm_source=chatgpt.com), ELK's [reasoning model](https://github.com/liveontologies/elk-reasoner?utm_source=chatgpt.com), Jena's [hybrid reasoning](https://jena.apache.org/documentation/inference/index.html?utm_source=chatgpt.com), and [Differential Dataflow](https://docs.rs/differential-dataflow?utm_source=chatgpt.com).&#x20;

[image](https://www.google.com/s2/favicons?domain=https://souffle-lang.github.io\&sz=32)

souffle-lang.github.io

+5



### The five techniques I would borrow first

1\. Soufflé's relation-centered execution. Its index planning and specialized relation structures are directly applicable to GestaltDB. The lesson is that operators should work over indexed tuples and integer symbols, not Python objects.

2\. RDFox's incremental-maintenance algorithms. Particularly interesting are DRed, backward/forward deletion strategies, counting, and full rematerialization as competing choices. There is no universally optimal deletion algorithm. Published RDFox research explicitly compares their crossover points.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://ojs.aaai.org\&sz=32)

ojs.aaai.org

+1



3\. VLog's compact intermediate representation. Materializing every inferred fact as a separate ordinary object can become extremely expensive. Shared representations, compressed blocks, and specialized storage for recursive results are worth investigating before investing in more hardware.

4\. Jena's hybrid execution modes. Simple, frequently queried hierarchies are good candidates for materialization; expensive, sparsely queried relationships may be better handled on demand.

5\. ELK's separation of ontology classification from instance reasoning. Computing a class hierarchy is different from propagating millions of individual facts through that hierarchy. GestaltDB should be able to classify schema-level knowledge once, then reuse the resulting schema closure for instance-level inference.

My strongest recommendation is to implement the Soufflé-style execution primitives first, then introduce RDFox-style maintenance. A differential-dataflow architecture becomes particularly interesting if the product eventually needs high-frequency streaming updates.

## 5. Proposed high-performance architecture

Python public APIs and query adapters

GraphDB · ontology definitions · Cypher · future RDF/SPARQL adapters

Ontology compiler and planner

Axiom normalization · typed rule IR · dependencies · SCCs · index selection · join order

Native reasoning runtime — Rust or C++

In-process, batch-oriented execution

Indexed joins

Semi-naive fixpoint

Delta propagation

Truth maintenance

Equivalence/closure

Interval operations

Native key/value batches

Arrow C Data/Stream

RocksDB / PyRex

Canonical history · persistent indexes · derived generations · metadata · durability

Arrow / Polars

Columnar inputs · intermediate batches · analytics · results

### Three representations, each with a distinct purpose

| Representation                  | Purpose                                   | Design choice                                           |
| ------------------------------- | ----------------------------------------- | ------------------------------------------------------- |
| RocksDB canonical graph/history | Persistent truth and provenance           | Retain existing storage architecture                    |
| Native working relations        | High-throughput joins, deltas and closure | Compact integer columns, hashes, sorted arrays, bitmaps |
| Arrow batches                   | Exchange, bulk operations, analytics      | Zero-/low-copy boundaries with Polars and Python        |

The native working representation should not be forced to use RocksDB's key-value layout internally. A hash join benefits from memory-local hash tables. A recursive hierarchy may benefit from integer adjacency lists or compressed bitmaps. A sorted merge join benefits from ordered arrays.

RocksDB remains responsible for persistence, recovery, and spilling larger working sets.

### Suggested language-independent operator interface

The following is a proposed logical contract, not an existing GestaltDB API. The concrete implementation can use Rust, C++, or an Arrow-based backend.

```
# Conceptual interfaces / pseudocodescan_relation(    predicate_id, bindings, context, read_horizon) -> FactBatchStreamjoin(    left, right, keys, strategy="auto") -> FactBatchStreamsemi_join(left, right, keys) -> FactBatchStreamanti_join(left, right, keys) -> FactBatchStreamfilter_terms(batch, expression) -> FactBatchproject(batch, columns) -> FactBatchdistinct(batch) -> FactBatchunion(left, right) -> FactBatchStreamdifference(new_facts, known_facts) -> FactBatchStreamevaluate_fixpoint(    compiled_plan, base_relations, delta_relations) -> DerivationStreamapply_changes(    inserted_facts, retracted_facts, ruleset_version) -> MaterializationDeltapublish_derivations(    delta, source_horizon, ruleset_version) -> GenerationId
```

Further specialized operators can sit alongside this general algebra:

```
compute_transitive_closure(...)merge_equivalence_classes(...)intersect_validity_intervals(...)propagate_dependencies(...)detect_constraint_violations(...)explain_derivation(...)
```

The operator interface is more important than the original syntax of a rule. One frontend can compile RDF/OWL axioms into it; another can compile GestaltDB's current Horn rule definitions. The executor sees identical operators.

### Compile rules into a dependency-aware intermediate representation

For each rule:

1. Normalize atoms, constants, variable IDs, and typed terms.
2. Validate safety, supported semantic profile, and termination assumptions.
3. Construct the dependency graph between relations.
4. Compute strongly connected components and recursive strata.
5. Choose binding patterns, indexes, and join order.
6. Generate delta-sensitive plans for recursive predicates.
7. Execute with per-operator statistics and preserve provenance handles.

A compiled plan should be cacheable by the combination of ruleset version, schema/dictionary generation, inference profile, and relevant optimizer settings.

This naturally supports an `explain_plan` facility without requiring a query language.

## 6. RocksDB: optimizations and issues specific to GestaltDB

The current source suggests several high-leverage improvements.

### 6.1 Replace Python-based inference scans with batched native scans

`run_rules` currently loads eligible claims into Python fact dictionaries before evaluation.

Furthermore, `iter_claims_as_of` chooses a statement index or one available dimension index, collects candidate logical IDs, then resolves and filters the matching versions in Python. It does not implement a general multi-index intersection and native join strategy for arbitrary rule-body bindings.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+1



I recommend a PyRex-level operation akin to:

```
// Illustrative native interface
ScanFacts(
    predicate_id,
    optional_subject_id,
    optional_object_id,
    world_id,
    valid_time,
    snapshot,
    batch_size
) -> ArrowRecordBatchStream
```

The iterator should perform key decoding, bound-column filtering, and preferably temporal candidate pruning natively.

Benefits are not limited to raw C++ execution speed: they also eliminate millions of Python callback and object-construction operations.

### 6.2 Create reasoning-specific indexes

Suggested physical index patterns, using compact term IDs:

| Index         | Optimized access                                      |
| ------------- | ----------------------------------------------------- |
| `P-S-O`       | Facts with a particular predicate and subject         |
| `P-O-S`       | Facts with a particular predicate and object          |
| `S-P-O`       | Entity-centric subject lookup                         |
| `O-P-S`       | Reverse/entity-centric lookup                         |
| `P-world-S-O` | Context-local predicate scan                          |
| `P-time-...`  | Temporal candidate pruning, if justified by workloads |

Do not blindly maintain all permutations. This increases write amplification and storage costs in RocksDB.

A better policy is to select materialized permutations based on the reasoning workload, with separate handling for small ontology/schema relations and large instance relations.

RocksDB's ordered iterators and prefix features are useful here, but prefix extractors and prefix Bloom filters must be configured consistently with actual key layouts. Incorrect assumptions about prefix-seek behavior can lead to incomplete scans.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+1



### 6.3 Reuse the existing compact graph machinery

GestaltDB already has an important optimization in its sampler: reusable CSR snapshots, compact IDs, and NumPy/memory-mapped execution without repeated KV deserialization.

The reasoner should reuse that architectural idea—compact IDs and reusable native arrays—but not assume CSR is the ideal representation for every rule.

- Graph reachability and property-chain traversal can profit from CSR-style adjacency.
- General multi-atom joins need relational indexes.
- Equivalence needs union-find or a more deletion-aware equivalent.
- Temporal facts need interval-indexed relations.

### 6.4 Separate canonical facts from materialization generations

A robust derived-fact layout should distinguish:

- Canonical assertions and immutable evidence.
- Current visible derived conclusions.
- Derivation supports and reverse dependencies.
- In-progress materialization deltas.
- Published generation metadata.

A reasoning run should read a fixed source horizon. Derived results should be made visible through an atomic generation switch or equivalent commit protocol.

This is especially important because inference may span many RocksDB write batches.

### 6.5 Review current write atomicity

The nontransactional PyRex typed-edge columnar path issues separate native batch calls for edge records, outgoing adjacency, and incoming adjacency. Individual native batch calls are efficient, but the three calls together do not constitute one atomic publication unit.

This is not necessarily problematic for an append-only bulk-load contract, but it is unsuitable as the assumed consistency guarantee for published inference results.

The transactional PyRex configuration and GestaltDB's existing temporal commit/visibility machinery provide a better starting point.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+2



### 6.6 Address rule-catalog scalability

Current rule versions are stored in a serialized metadata catalog that is rewritten when a new rule version is created.

That is fine for a small rule set, but a large or frequently changing ontology would benefit from indexed rule-version records and explicit ruleset generations.

I would also bind every materialization and compiled plan to a precise ruleset digest, not just a fact-horizon identifier.

### 6.7 Avoid support explosion

The number of justifications can be much larger than the number of distinct inferred facts.

A fully connected relationship, for instance, can have enormous numbers of alternative paths supporting the same transitive conclusion.

Recording every possible explanation as an ordinary record can be prohibitive. GestaltDB should support a tradeoff between:

- Exact, fully enumerated justifications when feasible.
- Compact support counts or dependency summaries for maintenance.
- Lazy expansion of detailed explanations.
- Specialized compressed representations for reachability/equivalence relations.

Maintaining correct deletion semantics is the governing requirement; storing every path eagerly is not always necessary.

Research on optimized materialization has demonstrated substantial storage savings for transitive and union-style derivations.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://arxiv.org\&sz=32)

arXiv

+1



## 7. Is C/C++/Rust necessary? My assessment

Yes for the high-throughput execution core. No for the entire reasoning stack.

Native code is not mathematically necessary for ontological reasoning. It becomes an engineering necessity if GestaltDB aims to sustain its high-performance positioning while evaluating millions of facts, recursive closures, or frequent incremental updates.

The current ingestion optimizations make this especially important: fast loading into RocksDB does not help enough if every reasoning pass reconstructs millions of Python objects and repeatedly joins them in Python.

### What should run where?

| Component                              | Recommended implementation        | Reason                                                  |
| -------------------------------------- | --------------------------------- | ------------------------------------------------------- |
| Public APIs, ontology definitions      | Python                            | Flexibility, maintainability                            |
| Ontology normalization                 | Python initially                  | Not generally the primary hot path                      |
| Rule validation / IR building          | Python initially                  | Easier semantic evolution                               |
| Rule dependency/SCC planning           | Python initially                  | Modest planning workload                                |
| Cost-based planning                    | Python initially                  | Optimize once, execute many times                       |
| Key decoding and batched RocksDB scans | C++ in PyRex                      | Direct RocksDB access                                   |
| Symbol dictionary / ID encoding        | C++ or Rust                       | High-frequency memory work                              |
| Hash and merge joins                   | C++ or Rust                       | Main CPU-intensive operation                            |
| Deduplication / delta relations        | C++ or Rust                       | Repeated large set operations                           |
| Fixpoint scheduling/execution          | C++ or Rust                       | Avoid Python round-trips                                |
| Equality-class maintenance             | C++ or Rust                       | Large mutable data structures                           |
| Interval joins                         | C++ or Rust                       | Vectorized comparisons                                  |
| Large-scale dependency propagation     | C++ or Rust                       | High-volume graph/set traversal                         |
| Small modal formula evaluation         | Python initially                  | Existing bounded evaluator is sufficient until profiled |
| Explanation presentation               | Python                            | Low-frequency boundary work                             |
| Arrow interoperability                 | Native binding plus Python facade | Zero-/low-copy transfer                                 |

### C++ versus Rust

C++: lowest-friction RocksDB integration

Extending PyRex is attractive because RocksDB itself is C++, and PyRex already bridges GestaltDB to native storage operations.

This minimizes the number of internal interfaces and permits close control over iterator lifetime, RocksDB snapshots, batching, and the block cache.

Rust: attractive reasoning runtime

Rust is well suited for memory-safe parallel algorithms, compact typed data structures, and Arrow interoperability.

It is especially interesting if differential dataflow, concurrent fixpoint execution, or a reusable standalone reasoning library become central to the project.

My preferred implementation choices, in order:

1. Simplest path: C++ reasoning operators integrated into the existing PyRex extension, exposed through batched Python APIs.
2. Good long-term alternative: C++ PyRex for RocksDB reads/writes, Rust for reasoning, and Arrow C Data/Stream as the principal interop boundary.
3. Less desirable: A separate Rust reasoner repeatedly calling Python/PyRex for individual database operations or maintaining an unsynchronized second RocksDB handle.

If Rust is selected, the native reasoning runtime should own an entire evaluation batch or fixpoint iteration, not individual tuple-matching calls.

I would avoid mixing Rust, C++, and Python inside the same inner loop.

### Should you just reuse Polars for joins?

For an initial performance prototype, absolutely consider it.

Polars already supplies native columnar filtering, joins, grouping, deduplication, and efficient Arrow interoperability.

An early prototype could:

1. Export fact batches into Arrow/Polars.
2. Express normalized rule-body joins using Polars LazyFrames.
3. Execute and materialize deltas.
4. Repeat until a fixpoint.
5. Compare its outputs and performance with the existing Python evaluator.

This would establish a strong baseline without immediately creating a new native relational engine.

But Polars is not a purpose-built recursive incremental inference engine. Repeatedly reconstructing execution plans, maintaining large recursive intermediates, and supporting provenance-heavy deletion may justify a specialized runtime.

My advice is to benchmark Polars as the intermediate solution, not assume that bespoke C++/Rust will automatically be faster.

### Differential Dataflow: build now or later?

Differential Dataflow can incrementally update iterative relational computations when records are inserted or removed. That is directly relevant to a live reasoning engine.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.rs\&sz=32)

Rust

+1



However, adopting it means managing its collection/update-time semantics, trace arrangements, iterative execution, memory use, and integration with RocksDB's persistent commit model.

I would implement a conventional native semi-naive engine first, then benchmark a Differential Dataflow proof of concept for workloads where frequent incremental changes are the dominant cost.

If incremental reasoning is the product's defining workload rather than periodic materialization, this order may be worth reversing.

## 8. Zero-copy: what is and isn't achievable

The right performance goal is avoid per-row Python marshalling and unnecessary buffer copies.

It is not realistic to demand literal zero-copy across every boundary.

### Data movement assessment

Polars → Arrow → native reasoner

Zero-copy possible

Native columnar relations → Arrow result

Zero-copy possible

RocksDB block cache → native consumer

Conditional pinning

RocksDB stored values → contiguous Arrow arrays

Usually conversion/copy

The bars are qualitative feasibility indicators, not performance measurements.

### 8.1 Polars and Arrow

Polars supports the Arrow PyCapsule interface and Arrow C Data interoperability, which can permit sharing existing buffers without materializing Python lists.

The preferred native binding should consume Arrow C Stream batches directly, including their ownership and release callbacks. This also avoids making PyArrow a mandatory dependency for every optimized operation.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.pola.rs\&sz=32)

Polars user guide

+2



Note that zero-copy is conditional on compatible types and layouts. Dictionary recoding, casting, rechunking, serialization, and some string representation changes can still allocate.

### 8.2 RocksDB and pinned values

RocksDB's `PinnableSlice` can avoid some copies when reading values, particularly when values are already resident in block-cache memory.

It does not promise universal zero-copy reads. RocksDB documents situations where a copy is still necessary, including certain memtable and merged-value cases.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://rocksdb.org\&sz=32)

RocksDB



There is also a deeper structural mismatch: RocksDB stores individual key-value records, whereas an ordinary Arrow variable-length array typically wants structured offset and data buffers.

Turning many independent RocksDB values into one ordinary Arrow binary/string column usually requires gathering or repacking data. String-view-based arrangements can sometimes avoid copying string payloads, but ownership becomes more complex.

I would not spend the first native-development milestone trying to expose arbitrary RocksDB memory as Arrow arrays.

Instead:

- Scan and decode inside native code.
- Intern strings and map entities to compact IDs.
- Build efficient working columns.
- Avoid Python objects entirely during joins.
- Export Arrow only at a useful batch boundary.

One necessary copy into a cache-friendly representation can be much faster than zero-copy access to an inefficient representation.

### 8.3 The critical native interface

Rather than exposing individual `get`, `put`, and iterator operations to the Python reasoner, add interfaces along these lines:

```
scan_predicate_batch(...)
scan_temporal_facts_batch(...)
encode_symbols_batch(...)
lookup_fact_ids_batch(...)
apply_derived_delta_batch(...)
```

Each returns or consumes hundreds to many thousands of records per invocation, with the batch size chosen experimentally.

Use Arrow streams for general columnar exchange and compact native fixed-width types for the internal join hot path.

### 8.4 Lifetime and snapshot guarantees

Zero-copy sharing introduces a correctness obligation: a native buffer cannot be released, changed, or invalidated while Polars/Python still references it.

The implementation needs explicit ownership rules covering RocksDB snapshots, pinned slices, Arrow buffers, iterator advancement, cancellation, and concurrent writers.

For long-running inference, I would strongly favor a fixed source-horizon read view and owned/retained native batch buffers over long-lived borrowed iterator memory.

## 9. Specific technical risks I would address before expanding semantics

Four implementation details deserve attention because they could affect both correctness and performance.

| Risk                          | Observation                                                                                                           | Proposed action                                                             |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Rule evaluation complexity    | Python nested loops scan predicate-specific candidate lists and create binding dictionaries                           | Introduce indexed, delta-specific native joins                              |
| Temporal closure completeness | Existing rules evaluate at a given `as_of` instant                                                                    | Introduce interval-set derivations before claiming full historical closure  |
| Long key encoding             | The key-value indexing layer uses base64 components and a 511-byte portable key limit                                 | Use compact IDs and versioned binary key encoding for reasoning indexes     |
| Rule/data horizon consistency | Rule versions live in metadata with their own system-time version history, while claims have temporal commit horizons | Pin a rule-catalog digest and fact horizon together in each materialization |

The key-encoding issue may be particularly relevant to ontologies because IRIs and composite index terms can be long. Compact identifiers would solve both a performance problem and a portability problem.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub

+2



Two additional tests are worth prioritizing:

Support cycles. Create a recursive support cycle, remove its only independent assertion, and confirm the conclusion disappears.

Temporal gaps. Create disjoint support intervals, derive a conclusion, and verify that no unsupported interval appears between them. Repeat the test with overlapping premises and version corrections.

These are important soundness checks for any future native replacement. Refresh note: recursive support-cycle removal is already covered by `tests/test_truth_maintenance.py::test_maintenance_matches_a_fresh_full_recomputation_and_collapses_cycles`; support narrowing and overlapping validity unions also have regressions. General disjoint-interval full-history closure remains unimplemented and must not be inferred from those tests.

## 10. Recommended implementation roadmap

I would implement this in eight milestones, with each milestone producing a measurable improvement or capability rather than a large, untested rewrite.

#### M0

Define semantics and establish the baseline

Current status (29 October 2026): **Partial**. Existing rule values and functional tests provide a baseline, but the compiled relational IR, predicate dependency graph/SCC scheduling, reasoning `explain_plan`, and dedicated inference benchmarks remain open.

Create a typed rule IR, dependency graph, correctness tests, and reasoning-specific benchmarks. Keep the existing Python evaluator as a reference implementation.

Exit criterion: repeatable closure results, execution profiles, and testable semantic contracts.

#### M1

Compact IDs and native RocksDB scans

Implement symbol encoding, predicate-specific iterators, native key decoding, batched reads, and Arrow C Stream outputs. Preserve existing storage and temporal API compatibility.

Exit criterion: remove Python per-fact processing from large reasoning scans.

#### M2

Native relational and recursive runtime

Implement hash joins, indexed joins, projection, distinct, set difference, delta joins, SCC scheduling, and fixpoint processing.

Exit criterion: same closure as Python reference, with measured time and memory improvements on large recursive workloads.

#### M3

Ontology compiler and first-class axiom operators

Add RDFS and an explicitly scoped OWL 2 RL subset: subclass/property inheritance, domains/ranges, inverse and symmetric properties, transitivity, equivalence, and disjointness checks.

Exit criterion: published profile coverage and soundness/completeness tests for supported constructs.

#### M4

True incremental truth maintenance

Extend the existing justification model with indexed reverse dependencies, support-sensitive deletion, and DRed or backward/forward maintenance. Add atomic derived generations.

Exit criterion: correct update behavior under insertions, deletions, alternative derivations, and recursive cycles.

#### M5

Complete temporal interval inference

Support disjoint interval sets, native interval joins, temporal deltas, and full-history derived conclusions. Preserve point-in-time and system-time reproducibility.

Exit criterion: temporal closure correctness across multiple intervals and retractions.

#### M6

Goal-directed and hybrid evaluation

Introduce magic-set specialization, tabled subgoals where appropriate, and an optimizer that selects between materialization and on-demand inference.

Exit criterion: selective queries avoid generating irrelevant global closure.

#### M7

Advanced profiles and specialized reasoning

Complete remaining OWL 2 RL behavior, consider OWL 2 EL consequence-based classification, and add separately defined validation and richer contextual reasoning.

Exit criterion: independently verified standard-profile or explicitly documented subset behavior.

### What I would deliberately postpone

I would not prioritize full OWL 2 DL/tableau reasoning, distributed reasoning, a GPU executor, generalized probabilistic logic, or a new query-language frontend.

Those could become valuable, but none is a prerequisite for the high-performance reasoning kernel.

I would also postpone complete native rewrites of modal evaluation and explanation formatting until profiles show they are significant bottlenecks.

## 11. Benchmark strategy: how to validate the native investment

GestaltDB's published ingestion benchmarks already demonstrate that serialization, indexing, and backend configuration can change performance significantly. Those results are useful, but ingestion speed is not a substitute for reasoning throughput.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://mylonasc.github.io\&sz=32)

GestaltDB



I recommend a dedicated benchmark suite with the following workloads.

| Workload                                | What it reveals                            | Primary measurement                    |
| --------------------------------------- | ------------------------------------------ | -------------------------------------- |
| Deep subclass hierarchy                 | Recursive hierarchy propagation            | Closure time                           |
| Diamond-shaped inheritance              | Repeated equivalent derivations            | Deduplication and support memory       |
| Transitive graph closure                | Recursive joins and output explosion       | Derived facts/sec, peak RSS            |
| Multi-hop property chain                | Join planning and indexing                 | Tuple comparisons and elapsed time     |
| Large equivalence classes               | Equality propagation and canonicalization  | Union/lookup cost                      |
| Small insertion into large closure      | Incremental maintenance                    | Update latency                         |
| Support-removing retraction             | DRed and recursive maintenance             | Recomputed facts versus affected facts |
| Overlapping/disjoint temporal intervals | Temporal correctness and interval handling | Correctness, time and memory           |
| Selective ontology query                | Demand-driven versus full materialization  | Query latency, facts explored          |
| Contradictory assertions                | Four-valued and constraint semantics       | Correct result/explanation             |

For datasets, use synthetic stress tests alongside established ontology benchmarks such as LUBM and UOBM. The RDFox incremental-maintenance research includes reproducible workload designs involving these datasets and several alternative deletion algorithms.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.cs.ox.ac.uk\&sz=32)

cs.ox.ac.uk



### Measurements to capture

- Total closure time, including initial fact loading into working relations.
- Derived facts per second and distinct derivations per second.
- P50/P95 incremental-update latency.
- Number of candidate tuples examined versus successful joins.
- Peak resident memory, bytes spilled, and final persisted database size.
- RocksDB read/write bytes, block-cache behavior, compaction time, and write amplification.
- Per-operator CPU time, allocations, and bytes copied.
- Python-object construction counts and time spent crossing native boundaries.
- Results under varying core counts and data-skew distributions.
- Equality with the trusted reference implementation, including temporal and provenance outputs.

I would measure the following alternative implementations against the same test corpus:

| Candidate                                     | Role                                    |
| --------------------------------------------- | --------------------------------------- |
| Current Python rule engine                    | Semantic reference                      |
| Polars join/fixpoint prototype                | Low-development-cost optimized baseline |
| Native C++ or Rust evaluator                  | Main performance candidate              |
| Native evaluator with incremental maintenance | Dynamic-update candidate                |

Where available and license-compatible, Soufflé or RDFox can serve as external reference systems for comparable fragments, but comparisons must control for semantics, materialization policy, and persistence costs.

One important acceptance gate: a native implementation that derives the correct set of unique facts but loses justification provenance or temporal validity is not equivalent to the current engine.

## 12. The minimum valuable native reasoning engine

Out of the 136 tracked constructs, I would start with a much smaller, dependency-ordered kernel.

### Initial native kernel: implementation checklist

0 of 12 checked

Reset

Compact entity, class and predicate IDs

Indexed native RocksDB fact scans

Arrow batch import/export with safe ownership

Selection, projection and unification

Hash join and index nested-loop join

Set union, difference and deduplication

Per-atom delta semi-naive execution

Recursive SCC/fixpoint scheduling

Premise and rule justification references

Atomic derived-fact publication

Native operator statistics and execution limits

Python-reference differential correctness tests

&#x20;Copy checklist

This kernel would be useful even before full ontology-profile coverage. It can accelerate existing rules, property-chain-style inference, hierarchy propagation, and a broad range of graph-derived facts.

The key is to complete the data-processing foundation before continually broadening the inference syntax.

## 13. Final architectural recommendation

I would prioritize the project around these decisions:

First, make a typed relational reasoning IR the contract between semantics and execution. It allows GestaltDB to improve its existing Horn rules without becoming dependent on any particular logic language.

Second, extend the RocksDB/PyRex read path. GestaltDB already has a fast native write route; the missing companion is a batched native read/indexed-relation interface optimized for inference.

Third, build a compact native fixpoint executor. Dense IDs, indexed joins, delta relations, deduplication, and SCC scheduling should be the first performance milestone.

Fourth, preserve the distinctive temporal and epistemic capabilities. They are valuable differentiators, but they need explicit support in the native representation and incremental maintenance algorithms. Do not discard provenance or reduce four-valued reasoning to ordinary Boolean logic just to make the engine faster.

Fifth, implement ontology constructs as compilation targets. Subclass inference, inverse properties, transitivity, equality, domain/range inference, and disjointness should be compiled from ontology definitions to reusable execution plans, not implemented as unrelated Python algorithms.

### The resulting positioning

GestaltDB could occupy an interesting space between high-performance embedded property-graph storage, native relational analytics, and ontology-aware deductive reasoning.

The most compelling combination is:

Embedded RocksDB persistence + Arrow-native data exchange + compiled recursive reasoning + incremental truth maintenance + bitemporal provenance.

That is a more coherent technical direction than trying to add as many reasoning-language constructs as possible.

### Research limitations

This was a source-level architecture audit of the public repository and published documentation, together with a comparative study of established reasoning approaches. I did not execute GestaltDB's benchmark suite or compile and profile a native implementation in this assessment. Therefore, the performance recommendations are grounded engineering hypotheses, not claims of measured speedups for GestaltDB.

The most useful next engineering deliverable would be an M0/M1 technical design and performance experiment comparing the current Python fixpoint implementation, a Polars-based join prototype, and native batched RocksDB scans.

The 136-item feature tracker provides the broader backlog, priorities, and acceptance criteria for carrying that work forward.
