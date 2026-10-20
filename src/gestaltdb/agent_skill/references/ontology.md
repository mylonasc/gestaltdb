# Ontology reasoning

Import from `gestaltdb.ontology` and `gestaltdb.reasoning`, not the package root.
The default is a bounded, read-only, positive-Datalog closure over named entities.
It needs no embedded backend or solver extra. The existing package initializer
still loads other GestaltDB subsystems; separate installation is not promised.

```python
from gestaltdb.ontology import Domain, Ontology, SubClassOf, TYPE_PREDICATE, compile_ontology
from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits

plan = compile_ontology(Ontology("people", [
    Domain("worksFor", "Researcher"),
    SubClassOf("Researcher", "Person"),
]))
result = DatalogEngine().evaluate(
    plan.program, [Fact("alice", "worksFor", "acme")],
    limits=ReasoningLimits(), collect_proofs=True,
)
person = Fact("alice", TYPE_PREDICATE, "Person")
assert person in result
assert not result.explain(person).truncated
```

Supported axioms: named subclass/subproperty, named equivalence, domain/range,
inverse, symmetric and transitive object properties. `gestalt-core-v1` is not
complete RDFS/OWL-RL. Equality, existential generation, datatypes, negation,
cardinality, disjointness and automatic query/sampler expansion are not included.
Missing is not false; domain/range infer types, not required-field constraints.

`Variable("x")` is a rule variable; string `"?x"` remains a constant. Logical
facts are deduplicated. Resource exhaustion raises `ReasoningLimitError`, never
returns successful partial closure. Proof limits truncate explanations only;
one witness is not an all-supports or deletion-maintenance index.

For temporal claims use `gestaltdb.reasoning.adapters.claims.PinnedClaimSource`
with an existing `graph.read_view(...)`, explicit world, `ClaimSelection`,
`ClaimMapping` and the compiled ontology revision. It returns a context-bound,
point-scoped result, excludes recognized derived inputs by default and never
publishes claims. Do not concatenate evidence from incompatible contexts.
Close sources and graph handles explicitly. Stale indexes require a separate
caller-managed rebuild. Claim extraction can dominate kernel time; do not infer
bulk-read performance from standalone reasoning benchmarks.

## Explicit optional boundaries

The experimental `gestaltdb.reasoning.adapters.polars.PolarsDatalogPrototype`
(`gestaltdb[polars]`) returns a closure-only `PolarsRun` for explicit in-memory
contexts. It is not the default evaluator: no witnesses, source authentication,
publication or maintenance; its row-work budgets differ from indexed Python.

- `gestaltdb.ontology.adapters.rdf`: install `gestaltdb[rdf]`; pass an in-memory
  `RDFDocument` and absolute `ontology_id` to `import_rdf`. Imports resolve only
  through an explicit document map. Unsupported logic fails strictly; permissive
  reports are marked incomplete and require explicit opt-in to compile. No URL
  loading, JSON-LD contexts, XML DTD/entities or automatic `owl:imports` fetches.
- `gestaltdb.reasoning.adapters.graph`: borrow an open verifiable
  `graph.current_read_view(require_verifiable=True)` with `PinnedGraphSource` and
  explicit `GraphMapping` allowlists for labels, edge types and entity-valued node
  properties. This never reads temporal claims. The scoped result filters logical
  triples via `match` and samples distinct inferred endpoints via
  `sample_neighbors(node, predicate=..., direction="out", fanout=..., seed=...)`.
  Expansion precedes fanout; parallel edges/proofs do not multiply weights. This
  is not a Cypher rewrite or an array-sampler mutation.
- `gestaltdb.reasoning.adapters.publication.reconcile_point`: explicitly
  recompute latest positive base evidence under the temporal writer lock and
  reconcile a managed namespace in a **separate target world**. Validity is just
  `[t, t+1 microsecond)`. No stale result or arbitrary output interval is accepted.
  Whole closure recomputation handles cyclic self-support, independent support
  removal and revision changes. Nontransactional interrupted publications can
  require explicit quiesced orphan reclamation/index rebuilding before retry;
  no silent repair occurs. Use the publication Sphinx guide for that workflow.

Interval closure, incremental all-support indexes and automatic query/weighted
sampler integration remain explicit follow-ups, not implied features.
