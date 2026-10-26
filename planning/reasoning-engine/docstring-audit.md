# High-level docstring usability pass (#387)

Release finishing pass on `release/reasoning-next-candidate`, under #385/#386.
Scope: component entry points and the common application handoffs, not every
low-level helper, result field, or experimental operator. No executable production
AST changes: stripping docstrings yields the same AST in all 21 touched Python
files. No merge, version bump, tag, or publication.

## Executable, self-contained docstrings

`tests/test_public_workflow_docstrings.py` executes each entry below in a fresh
namespace, with imports and input construction taken directly from its public
docstring. No graph/program/facts fixture is injected and no doctest SKIP is
allowed. Optional backend examples skip only in intentionally minimal installs;
the release full-suite preflight requires real backends and serializers.

| Component | Tested entry points | Application path |
|---|---|---|
| Database | `GraphDB`, `GraphDB.ingest_arrow` | Create/query/reopen; load entity columns and a typed edge |
| Recorded history | `GraphDB.read_view` | Compare a claim before/after retraction using a recorded prefix |
| Stored rules | `GraphDB.run_rules` | Derive, persist, explain, retract evidence, maintain conclusions |
| KV backends | `LMDBStore`, `LevelDBStore`, `PyRexStore` | Raw byte storage with real stores and explicit close |
| Serialization | `JSONSerializer` | Encode/decode a payload independently of backend selection |
| Sampling | `SamplerSnapshot`, `SamplerEngine` | Build/reload arrays, sample by external IDs, convert output |
| Ontology | `compile_ontology` | Compile class axioms, infer classification, inspect a witness |
| Binary/typed evaluation | `DatalogEngine`, `GenericEngine`, `GenericEngine.evaluate_stratified` | Facts to closure, typed filters, explicitly scoped absence |
| Execution adapter | `execute_closure` | Default indexed execution with result and telemetry wrappers |
| Selective queries | `answer` | Bound-goal evaluation for one entity |
| Incremental supports | `rematerialize` | Build a support index and remove a derived fact after premise loss |
| Temporal inference | `evaluate_range` | Half-open validity, derived point answers, conformance check |
| Validation | `validate` | Report conflicting functional values without changing facts |
| Graph projection | `PinnedGraphSource` | Explicit label mapping under a verifiable current read view |
| Saved reasoning state | `start_session` | Start/advance/save/reload with exact-file-state comparison |
| Visualization | `visualize_nodes_edges` | Construct and save SVG without a database or browser |

Total: **22 independently executed docstring workflows**. Backend examples use
disposable directories and `try/finally`; array examples use RAM loading so their
temporary directories have simple cross-platform lifetimes. Examples explicitly
show byte IDs at database read boundaries and `.result.facts` on wrapped results.

Final validation: full optional suite **3,395 passed / 3 expected capability or
fault-injection skips**, with no missing-backend skips; Sphinx warnings-as-errors,
8 guide examples, API-index drift and roadmap checks passed. The earlier full run
failed only because the generated source-line API index was stale; regeneration
and this complete rerun resolved that failure.

## Long workflows: inline duplication deliberately skipped

The following require database/program setup plus trusted identities, source
policies, staging, or artifact selection. Compressing that setup into a few lines
would hide important application decisions. Instead, entry-point docstrings name
the inputs and link to one complete guide:

| Workflow | Complete runnable guide | Existing regression |
|---|---|---|
| Source-verified durable resume | `docs/reasoning-authenticated-sessions.rst` | `test_authenticated_session_documentation_example` |
| Bound interval publication | `docs/reasoning-bound-range-publication.rst` | `test_bound_range_publication_documentation_example` |
| Source-verified inferred sampler | `docs/reasoning-authenticated-inferred.rst` | `test_authenticated_inferred_documentation_example` |
| Native full-record claims | `docs/reasoning-native-claims.rst` | `test_native_claim_documentation_example` |

The new `docs/api-workflows.rst` exposes the reasoning docstrings through Sphinx
and links these larger workflows. Existing API pages render database, backend,
sampling, serialization, and visualization class examples. Model-to-inference
audit examples remain in `docs/inference-audit.rst`; other policy-heavy modal,
prepared-plan, optional-executor, and experimental-profile workflows remain in
their subsystem guides rather than being duplicated in every public docstring.

## Remaining usability boundaries

* The library has two rule-execution families: stored temporal rules and
  standalone Datalog. Examples now make persistence versus returned closure
  explicit; they do not make the APIs interchangeable.
* Result wrappers and ID representations differ. The examples demonstrate the
  actual fields/conversions rather than assuming one common return shape.
* Source-verified reuse still requires several explicit application-held pins and
  policies. Guides document them; this pass does not introduce a convenience API
  that silently chooses trust or publication policy.
* This is not a promise that every legacy method example has been converted from
  contextual/disabled snippets to standalone doctests. The selected component
  workflows now provide executable starting points and guide links.
