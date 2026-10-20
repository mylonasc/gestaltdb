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
