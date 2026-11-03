# Documentation authoring contract

Applies to DOC-01–DOC-13 under #480. This is guidance for authors, not an additional
runtime API contract. Verify exact behavior against the source and tests cited in
the capability matrix.

## Audience and progressive structure

Every page has a primary audience and one primary role: overview, tutorial,
how-to, explanation, reference, operations or engineering evidence. Introductions
answer **what problem, when to use it, and what it is not** before implementation
details. Lead with one small runnable result, then concepts, common tasks, limits,
API links and advanced evidence. Avoid imposing arbitrary line limits on reference
pages; keep entry points short by linking detail rather than removing caveats.

Do not assume that a capable Python/database user knows logic-programming terms.
Introduce nonstandard terms such as witness, closure, stratum and grounded atom
in plain language before code uses them. Link to `docs/glossary.rst` and the
facts/rules introduction; optional external background is supplementary, never
the only explanation. Show one concrete meaning before abbreviated terminology.

Use property-graph developer, graph-ML developer, knowledge-graph developer,
ontology/Datalog user, reasoning integrator and operator as the shared audience
names. Contributor and coding-agent material has a separate retrieval/development
role and must not displace user tutorials.

## Library-focused publication (required)

Published guides describe the library as it exists: current APIs, supported
workflows, limitations and compatibility requirements. They must not read as
implementation diaries, issue acceptance reports, release-candidate checklists,
roadmap delivery slices or interim adoption decisions. Do not use issue/phase
IDs as headings, tell users what a later milestone will implement, or narrate
regression investigation, CI recovery or release publication steps.

State unsupported features directly rather than calling them future work.
Keep actual format/API identifiers such as `indexed-python-v1` and necessary
minimum dependency versions: these describe compatibility, not project phases.
Benchmark evidence may retain source revisions, environments, methodology and
limitations; it must not turn old measurements into claims about current wheels.
Development plans and investigation history belong in `planning/`, not the
published Sphinx navigation. Audit prose as well as code before calling a guide
complete; passing Sphinx is not an editorial quality check.

## Terminology

| Term | Use precisely |
| --- | --- |
| Current graph | Mutable nodes/edges and indexes. Ordinary writes do not automatically create immutable temporal history. |
| Backend | KV persistence implementation: LMDB, LevelDB or PyRex/RocksDB. Describe transaction/snapshot capabilities per backend and runtime version. |
| Serializer | Independently selected payload codec: Pickle, JSON, MessagePack or Protobuf. Backend and codec are not interchangeable terms. |
| Read view | Explicitly name current versus temporal view and whether its identity is verifiable. Read-only authority alone is not a snapshot or source authentication. |
| Valid time | When a fact/version applies in the modeled world. Intervals use half-open bounds. |
| System time / commit horizon | Recorded-history selection, not another name for valid time. State which public parameter a workflow uses. |
| Claim | Sourced proposition with polarity, world and agent/source metadata. A positive claim is not automatically true in reality. |
| Absence | Scoped failure to find a positive fact under a declared closed-predicate policy; not an explicit negative claim. |
| Stored rule | `graph.rules` evaluation over selected temporal claims; not the same API or capability set as standalone Datalog. |
| Ontology compilation | A selected axiom fragment compiled to a program; not full OWL/RDFS/SHACL reasoning or automatic graph rewriting. |
| Closure | Consequences under the specified program and selected input facts; distinguish full closure from selective goal answers. |
| Temporal reasoning | Computing conclusion validity intervals; storing temporal versions alone does not run it. |
| Witness / support | A first-witness explanation is not an all-support maintenance index or independent proof certificate. |
| Authentication / admission | Verification of expected source/state identity under the API's contract; not login, truth verification or automatic publication permission. |
| Publication | Explicit guarded persistence of inferred claims, separate from evaluation and read-only consumption. |
| Snapshot sampling | Array-native draws from a built immutable artifact; not live KV traversal. |
| ID domain | Distinguish external IDs, compact global snapshot IDs and batch-local rows. Show conversions explicitly. |
| Confidence | Metadata, unless an explicitly supported calculus says otherwise; do not call it a logical probability. |
| Experimental / deferred | Not a default supported execution route. Explain opt-in scope or lack of implementation. |

## Runnable examples

Keep `docs/glossary.rst` as the canonical user terminology section; add unfamiliar
jargon there and use Sphinx `:term:` links at useful introductions. The terminology
extension derives short tooltip definitions from that file, retaining ordinary
glossary navigation and a native title fallback without JavaScript. Enhanced
tooltips support hover, keyboard focus and Escape dismissal.

Declare applicable backend/serializer alternatives in
`docs/_ext/example_options.json` (page name plus zero-based Python-block index).
Keep one canonical source block; `database_examples.py` renders real nested
`sphinx-tabs` with installation commands and AST-rewritten configuration only.
Every declaration explains its capability boundary. Transaction/current-snapshot
examples exclude incapable backends; native admission stays plain-PyRex-only;
raw bytes exclude JSON; Pickle is trusted-data-only. Never imply equal performance.
Backend-free snippets and existing-graph continuations need no selectors. Fixed
benchmark setups have an explicit non-rendered disposition; notebook/shared-state
setup fragments are compiled, not advertised as independently executable programs.
Run `tests/test_documentation_variants.py` with all backend, serializer, Arrow,
Polars and NetworkX extras to execute alternatives and detect undeclared examples.

Label each block as standalone, continuation, illustrative fragment or pseudocode.
Standalone examples include imports, data construction, optional extras and expected
results. Continuations explicitly name their setup on the same page; fragments must
not look like copy-paste-complete programs. Use `TemporaryDirectory`, supported
Python versions, and `with GraphDB.create(...) as graph` or `try/finally` closure.
Use explicit submodule imports for graph entities/backends/serializers. Do not
silently fall back when an optional dependency is missing.

Small drug/protein/evidence datasets may be reused across pages, but each page
must define its own required state. Give one example one primary teaching goal;
do not hide it behind a production-scale setup. Prefer stable expected IDs or
sets over incidental iteration order. Include failure/unsupported boundaries near
the operation, not only in an end-of-page warning.

## Mathematical notation and pseudocode

Use notation where it clarifies a rule or operation, always with a plain-language
reading and a mapping to the API/example. Sphinx supports `.. math::` and `:math:`;
do not add a second rendering dependency. Define every symbol locally and avoid
notation in place of explaining prerequisites or lifecycle.

Recommended teaching conventions (not new APIs):

- Property graph: a directed multigraph `G = (V, E, s, t)` with source/target maps
  `s, t: E → V`. Edges have identity: distinct edges can share endpoints. Describe
  labels and properties separately rather than treating every property as an edge.
- Filtered graph: state whether filtering removes only seeds or changes eligible
  vertices/edges. A graph `G'` built with edge filters need not be vertex-induced.
- Sampling: define the eligible neighbor set/multiset, direction, relation policy,
  fanout, replacement and weighting before writing a sampling distribution. Do not
  use a uniform formula for weighted draws or collapse parallel edge occurrences.
- Datalog: read `H ← B1 ∧ … ∧ Bk` as “derive H when every body condition holds”.
  Explain variables, finite domains and rule safety before fixpoint notation.
  A simple positive full-closure exposition may use `F[n+1] = F[n] ∪ T_P(F[n])`;
  do not suggest this pseudocode is the physical semi-naive implementation or the
  contract of stratified, temporal or demand evaluators.
- Time: use `[a,b)` and show that `b` is excluded. Independent conjunctive premise
  validity intersects; alternative supports union. The statement assumes fixed
  selected premises and does not replace correction/retraction winner selection.
- Four-valued status: two independent support bits `(positive, negative)` explain
  supported/refuted/both/unknown; absence and classical Boolean negation are not
  interchangeable with explicit negative evidence.

Pseudocode blocks must be labelled and must identify inputs, outputs and relevant
failure conditions. Distinguish a semantic algorithm from an optimization sketch.
For bounded operations explain whether exhaustion raises, truncates an explanation,
or returns a flagged report; do not invent a universal partial-result policy.

## Visual opportunities

Mark proposals with the matching `VIS-*` identifier in `graphics.md` and the
graphics-debt issue. Use static diagrams before optional animation; alt text,
captions and readable static fallbacks are mandatory. No semantics should depend
on autoplay, color alone, a network fetch or interactive JavaScript. Prefer
small diagrams driven by the runnable fixture over decorative architecture art.

For actual graph/result examples, prefer `gestaltdb.viz.api` to a hand-drawn
replacement. `scripts/generate_documentation_figures.py` builds committed static
SVGs from the same small fixtures without a backend, browser or npm dependency.
Keep the existing Graphviz architecture map: it describes components rather than
data entities and is not interchangeable with an example graph visualization.

## Compatibility and evidence

Keep old page URLs where practical; splits leave a short hub or explicit routing
page. Describe ordinary/default routes before native/experimental alternatives.
Separate benchmark evidence and internal contracts from user introductions, but
preserve their links and qualifications. Never relabel an old measurement as a
new source/wheel result or imply that shorter documentation changes runtime scope.
