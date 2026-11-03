# Documentation restructuring: capability coverage and user journeys

**Reviewed at:** 2026-11-02 (UTC).

**Reviewed source:** [`8199b1d1025ae64967f8fa300f9542a324a15bd3`](https://github.com/mylonasc/gestaltdb/commit/8199b1d1025ae64967f8fa300f9542a324a15bd3), the v0.17.0 release source.

**Tracking parent:** [#480](https://github.com/mylonasc/gestaltdb/issues/480).

**Authority:** this document records the documentation assessment, target structure,
delivery slices and acceptance criteria. Source code and tests remain authoritative
for behavior. Documentation must not promote an experiment, benchmark result or
deferred feature into a supported guarantee.

## 1. Objective

Restructure GestaltDB's documentation so that a user can:

1. discover the relevant capability from an application goal;
2. understand when to use it and what it is not;
3. run a concise, complete example;
4. find exact semantics, limitations and operational requirements; and
5. progress to advanced integration material without starting there.

This is not a request to reduce the technical depth of the documentation. The
repository contains valuable correctness contracts, benchmark evidence and design
boundaries. The goal is to put concise user journeys in front of that material,
classify the detailed pages by role and make their navigation intentional.

## 2. Review baseline

The review sampled the landing pages, quickstart, architecture and API workflows;
the major sampling, temporal, epistemic, ontology and reasoning guides; the
packaged agent skill; the repository agent map; and the complete Sphinx page
inventory.

Observed baseline:

- The Sphinx tree contains **71 RST pages**. The current `docs/index.rst` names
  36 pages in its toctrees; most of the remaining pages are advanced reasoning
  references marked `:orphan:`.
- `sphinx-build -W --keep-going` succeeds for all 71 pages.
- The documentation/API drift checker succeeds. It checks 18 Markdown documents,
  selected public signatures, imports, registries and referenced Sphinx files;
  it does not establish that every RST example is complete or that the narrative
  is discoverable and progressive.
- Sampling has the clearest first-class task path. `typed-sampling.rst` is also
  about 888 lines, so the introductory path competes with format, compatibility
  and advanced-policy detail.
- `performance.rst` is about 970 lines, `cypher.rst` about 644 lines,
  `temporal-versioning.rst` about 533 lines and `typed-sampling.rst` about 888
  lines. These are useful references, but size and mixed roles reduce digestibility.
- The quickstart covers graph creation, direct reads, indexes, Cypher, traversal,
  bulk writes and reusable sampling. Its next steps do not currently route users
  to temporal history, epistemic claims, ontology, standalone Datalog or temporal
  reasoning.
- The landing page foregrounds filtering and sampling, then places a mixture of
  introductory and advanced reasoning integrations in one broad “User Guide”.
  There is no equivalent Datalog/reasoning learning-path hub.
- The ontology guide has useful examples and strict profile boundaries, but begins
  with optional RDF import before its shortest standalone classification example.
- Several reasoning pages provide precise contracts but assume users already know
  how the pieces compose. For example, the short temporal-closure snippet refers
  to an undefined `program` and omitted typed-engine imports; it is illustrative,
  not a standalone example, but is not labelled as such.
- The packaged retrieval CLI exposes an ontology topic, while the packaged skill's
  prose topic list omits it. Packaged topics cover temporal and epistemic storage
  but do not provide a general standalone Datalog/reasoning topic.

The baseline therefore passes structural and API checks but does not yet prove
complete capability coverage, concise introductions or successful audience
journeys.

## 3. Assessment against the review questions

### 3.1 Does the documentation reflect the library's capabilities?

**Mostly at the detailed-reference level, but not yet as one coherent public
capability map.** The repository documents almost every major subsystem somewhere.
The strongest risk is not total absence; it is that capabilities are distributed
between landing pages, very large guides, orphaned reasoning pages, docstrings,
agent references and roadmap-style contract documents.

The restructuring must cover all of these supported surfaces:

| Capability family | Supported surfaces that require a documentation disposition | Delivery |
| --- | --- | --- |
| Core property graph | Nodes, native labels, directed edges, typed relationships, stable IDs, object/batched CRUD, current readers/repositories | [DOC-11 #491](https://github.com/mylonasc/gestaltdb/issues/491) |
| Swappable persistence | `GraphDB.create/open`, manifests, direct store construction, LMDB, LevelDB, plain/transactional PyRex, locking, transactions, current snapshots and close/reopen lifecycle | DOC-11 #491 |
| Serializers and values | Pickle, JSON, MessagePack, Protobuf, independent backend/serializer extras, recursively persisted temporal values | DOC-11 #491 |
| Indexes and filtering | Automatic label/type indexes, explicit property indexes, exact/range/composite lookups, ID-only cohorts, freshness, deferred rebuilds and key limits | [DOC-12 #492](https://github.com/mylonasc/gestaltdb/issues/492) |
| Ingestion | Object/bulk writes, Arrow/Polars, entity columns versus serialized payloads, maintain/defer/defer-rebuild and native writer boundaries | DOC-12 #492 |
| Cypher | Reads, label expressions, typed/fixed/variable/shortest paths, expressions, aggregation, composition, temporal qualifiers, writes, constraints, schema inspection and registered procedures | DOC-12 #492 |
| Sampling | Live typed traversal, seed reservoirs, snapshots, compact/local/external IDs, RAM/memmap, filtering, weighted/balanced layers, blocks, subgraphs and hierarchy expansion | [DOC-03 #483](https://github.com/mylonasc/gestaltdb/issues/483) |
| Walks and ML preparation | Random/Node2Vec/policy walks, schemas, temporal walk policy, context pairs, negatives, temporal positives and framework-facing blocks | DOC-03 #483 |
| Temporal/bitemporal data | Immutable versions, valid/system time, corrections/retractions, indexes, authenticated read views, point/window reads, bitemporal Cypher, migration and recovery | [DOC-04 #484](https://github.com/mylonasc/gestaltdb/issues/484) |
| Epistemic knowledge | Sourced claims, polarity, agents/worlds, confidence metadata, four-valued status, corrections/retractions and provenance | [DOC-05 #485](https://github.com/mylonasc/gestaltdb/issues/485) |
| Stored rules and modality | Versioned Horn rules, scoped absence, maintenance, explanations, accessibility, belief/knowledge/possibility/necessity and audit export | DOC-05 #485 |
| Ontology | Immutable axioms, selected profile, compilation/origins, offline RDF, hierarchy adapters, graph/claim projection and explicit publication | [DOC-06 #486](https://github.com/mylonasc/gestaltdb/issues/486) |
| Standalone Datalog | Binary and typed engines, recursion, literals, comparisons, multi-heads, predicate variables, stratified negation, aggregates, proofs, demand, equality and validation | DOC-06 #486 |
| Temporal reasoning | Interval sets/facts, change segments, exact range closure, time-scoped equality, selected negation/aggregates, maintenance, conformance and interval evidence | [DOC-07 #487](https://github.com/mylonasc/gestaltdb/issues/487) |
| Advanced reasoning integration | Current/temporal extraction, frontier/full closure, plans/executors, batches, sessions, generations, admission, native sources, inferred consumers and publication | [DOC-08 #488](https://github.com/mylonasc/gestaltdb/issues/488) |
| Visualization | Offline HTML/Jupyter, static SVG, query/sample/reasoning builders, layouts, styling, browser export and saved view state | [DOC-13 #493](https://github.com/mylonasc/gestaltdb/issues/493) |
| Interoperability | NetworkX import/export, static GEXF, mapping/directionality/security limits and notebooks | DOC-13 #493 |
| Operations and performance | Installation/extras, compatibility, index state, migration/recovery, vulnerability workflow, workload-specific ingestion/query/sampling/reasoning measurements | DOC-11 #491 and DOC-13 #493 |
| API and agent surfaces | README, Sphinx, `EXAMPLES.md`, package docstring, public docstrings, `AGENTS.md`, packaged skill and `agent_docs` retrieval | [DOC-10 #490](https://github.com/mylonasc/gestaltdb/issues/490) |

The capability inventory in [DOC-01 #481](https://github.com/mylonasc/gestaltdb/issues/481)
must verify this provisional map against source and tests and record any additional
public surface rather than silently dropping it.

### 3.2 Are capabilities introduced concisely with digestible examples?

**Unevenly.** The core quickstart, filtering guide and several sampling workflows
are practical. Ontology and many reasoning pages contain examples, but they often
sit within contract-heavy pages or assume concepts and setup from elsewhere.
Some code blocks are fragments rather than complete workflows. Long pages mix
introductory guidance, exhaustive reference, benchmark evidence and compatibility
history.

Every major capability needs a layered path:

1. problem and appropriate use;
2. concise runnable example with expected output;
3. conceptual model and choice table;
4. common task guides;
5. limitations and lifecycle requirements;
6. API map; and
7. advanced contracts and evidence.

The initial example should not attempt to demonstrate every option. Detailed
semantics remain in reference pages and should be linked at the point where a user
needs them.

### 3.3 Are special capabilities given adequate focused sections?

**Sampling: yes in depth, but it needs progressive splitting. Ontology: substantial
coverage, but its first-run journey should precede RDF and advanced adapters.
Reasoning/Datalog: deep coverage without an adequate introductory hub. Temporal
storage: deep coverage but overloaded. Temporal Datalog: precise but largely an
advanced orphaned reference. Epistemic/modal reasoning: focused semantics but
needs a complete claims-to-rules-to-maintenance journey.**

The targeted slices below give each of these a first-class entry point while also
covering the less distinctive but equally important database, backend, ingestion,
Cypher, visualization and operations capabilities.

## 4. Target audiences

Review and acceptance use six representative audiences:

1. **Property-graph application developer:** CRUD, persistence, indexes, Cypher,
   ingestion, current snapshots and visualization.
2. **Graph-ML developer:** cohort filtering, snapshot construction, ID domains,
   neighborhoods/layers/walks, negatives and temporal sampling.
3. **Knowledge-graph developer:** temporal claims, provenance, contradictions,
   stored rules, modal evaluation and explanations.
4. **Ontology/Datalog user:** ontology compilation, facts/rules, typed literals,
   recursion, negation, aggregates, proofs and demand evaluation.
5. **Advanced reasoning integrator:** graph/claim adapters, authenticated sessions,
   admission, maintenance, interval evaluation/publication and executor choice.
6. **Operator:** installation, backend/serializer choice, concurrency guarantees,
   index freshness, recovery, migration, security and performance boundaries.

## 5. Target information architecture

The documentation landing page should offer a goal-oriented table and intentional
toctree groups rather than one flat user-guide sequence:

1. **Getting started** — installation, quickstart, architecture and goal map.
2. **Property graph and persistence** — graph model, backends, serializers,
   lifecycle and current read views.
3. **Querying and loading data** — filtering/indexes, ingestion and Cypher.
4. **Sampling and graph ML** — live versus snapshot choice, layers, walks,
   temporal policies and negatives.
5. **Temporal and epistemic knowledge** — version history, claims, rules, modal
   evaluation and audit.
6. **Ontology and Datalog** — standalone foundations, typed features, proofs,
   demand and validation.
7. **Temporal reasoning** — range closure, maintenance, admission and publication.
8. **Advanced reasoning integration** — source/executor/session/publication
   decision map and authenticated adapters.
9. **Visualization and integrations** — offline visualization, NetworkX/GEXF and
   notebooks.
10. **Operations, performance and reference** — compatibility, recovery,
    benchmarks, API reference and engineering evidence.

Advanced pages need not all appear directly in the top-level toctree. Every page
must, however, have an intentional role and an inbound path from a hub or reference
index. `:orphan:` should represent a deliberate Sphinx choice, not undiscoverability.

## 6. Example contract

Every code block must be classified as one of:

- **standalone runnable**;
- **runnable continuation** of setup on the same page;
- **illustrative fragment**; or
- **pseudocode**.

Introductory examples should:

- include all imports and define every variable;
- state required optional extras;
- use disposable paths and close graph handles safely;
- show expected results or assertions;
- demonstrate one primary concept;
- explain the important correctness/lifecycle boundary immediately after the code;
- use a small consistent application domain where that reduces cognitive load; and
- execute in CI where practical.

The existing Markdown/API checker is retained. [DOC-09 #489](https://github.com/mylonasc/gestaltdb/issues/489)
extends coverage to high-value RST workflows, Cypher snippets and installed-package
examples without making every illustrative fragment an expensive integration test.

## 7. Delivery slices

Each slice should be a small, independently reviewable series of PRs. Capability
guides may be written in parallel once they follow the provisional taxonomy; they
must reconcile with DOC-01 before closure.

| ID | Issue | Independently deliverable outcome |
| --- | --- | --- |
| DOC-01 | [#481](https://github.com/mylonasc/gestaltdb/issues/481) | Source/test-backed capability matrix, page roles and shared terminology. |
| DOC-02 | [#482](https://github.com/mylonasc/gestaltdb/issues/482) | README, landing-page and quickstart audience journeys. |
| DOC-03 | [#483](https://github.com/mylonasc/gestaltdb/issues/483) | Progressive sampling and graph-ML path. |
| DOC-04 | [#484](https://github.com/mylonasc/gestaltdb/issues/484) | Temporal/bitemporal data path. |
| DOC-05 | [#485](https://github.com/mylonasc/gestaltdb/issues/485) | Epistemic claims, stored rules and modal path. |
| DOC-06 | [#486](https://github.com/mylonasc/gestaltdb/issues/486) | Ontology and standalone Datalog foundations. |
| DOC-07 | [#487](https://github.com/mylonasc/gestaltdb/issues/487) | Temporal Datalog/range-reasoning guide. |
| DOC-08 | [#488](https://github.com/mylonasc/gestaltdb/issues/488) | Advanced reasoning integration hub and page classification. |
| DOC-09 | [#489](https://github.com/mylonasc/gestaltdb/issues/489) | Runnable-example inventory and stronger documentation CI. |
| DOC-10 | [#490](https://github.com/mylonasc/gestaltdb/issues/490) | Packaged-surface alignment and final audience acceptance. |
| DOC-11 | [#491](https://github.com/mylonasc/gestaltdb/issues/491) | Core graph, swappable backends, serializers and lifecycle. |
| DOC-12 | [#492](https://github.com/mylonasc/gestaltdb/issues/492) | Indexing, Arrow/Polars ingestion and Cypher. |
| DOC-13 | [#493](https://github.com/mylonasc/gestaltdb/issues/493) | Visualization, NetworkX/GEXF, installation, notebooks and performance. |

Suggested dependency flow:

```text
DOC-01 taxonomy ───────────────┐
                              ├─> DOC-10 final reconciliation
DOC-02 navigation ─────────────┤
DOC-03..08 capability guides ──┤
DOC-11..13 platform guides ────┤
DOC-09 example/CI gates ───────┘
```

DOC-03 through DOC-08 and DOC-11 through DOC-13 are intentionally parallelizable.
DOC-02 can land stable navigation groups before every linked guide is rewritten.
DOC-09 can add infrastructure incrementally. DOC-10 is the final reconciliation.

## 8. Review rubric

Score every capability from 0 (absent/incorrect) to 3 (complete and verified):

| Dimension | Completion standard |
| --- | --- |
| Accuracy | Claims and examples agree with source/tests and backend qualifications. |
| Discoverability | A user reaches the correct starting point from an application goal. |
| Introduction | The page explains the problem, appropriate use and non-goals. |
| Example quality | A minimal dependency-aware example runs and shows its result. |
| Conceptual clarity | Adjacent capabilities and lifecycle stages are distinguished. |
| Completeness | Supported public behavior has a guide/reference disposition. |
| Boundaries | Limits, authentication, budgets, atomicity and failure behavior are explicit. |
| Progression | Introduction leads to tasks, reference, then advanced contracts/evidence. |
| Cross-linking | Next steps and related surfaces are intentional and non-circular. |
| Concision | Entry points are skimmable; detail is layered rather than deleted. |

Priorities:

- **P0:** incorrect/unsafe claims, stale signatures or broken introductory examples.
- **P1:** supported major capabilities without discoverable introductory paths.
- **P2:** mixed page roles, duplication, excessive density and weak cross-linking.
- **P3:** wording, visual polish and optional additional examples.

## 9. Guardrails

- Preserve exact historical winners/evidence, source authentication, budgets,
  cancellation, cleanup and fail-closed behavior in all documentation claims.
- State backend-specific atomicity, snapshot and concurrency guarantees; do not
  imply that every backend supports the strongest mode.
- Keep ordinary extraction/default executor behavior visible. Optional native,
  Polars and experimental paths remain explicit opt-ins with measured boundaries.
- Confidence is metadata, not a logical probability. Model output is an assertion,
  not independent proof. Source authentication is not a truth guarantee or user
  authentication.
- Do not describe selected ontology fragments as full RDFS/OWL/SHACL support.
- Do not conflate current records, temporal history, interval reasoning, source
  admission and publication authority.
- Memmap, native scans and columnar paths are not universal zero-copy, out-of-core
  or speedup guarantees.
- Preserve engineering evidence. Move or classify it when necessary; do not erase
  it to make introductory pages shorter.

## 10. Epic exit criteria

- [ ] DOC-01 records every supported public capability and every documentation
  page/resource with an explicit role and disposition.
- [ ] Each target audience has a tested path from landing page to a successful
  representative workflow.
- [ ] Core graph, swappable backends/serializers, indexes, ingestion, Cypher,
  sampling, temporal/bitemporal data, epistemic/modal knowledge, ontology,
  standalone/typed Datalog, temporal reasoning, advanced adapters, visualization,
  integrations and operations all have discoverable entry points.
- [ ] Sampling, ontology, Datalog, temporal data, temporal reasoning and epistemic
  reasoning each have a concise overview, runnable introduction, conceptual model,
  limitations and API/advanced-guide map.
- [ ] Temporal storage versus temporal reasoning, standalone versus GraphDB-stored
  rules, live versus snapshot sampling, and evaluation versus publication are
  explicit distinctions.
- [ ] Introductory examples are complete or visibly labelled as fragments.
- [ ] README, Sphinx, `EXAMPLES.md`, package/public docstrings and packaged agent
  resources agree on imports, lifecycle, terminology and capability boundaries.
- [ ] Strict Sphinx, docs/API drift, example, link, packaged-resource and focused
  regression gates pass on final source.
- [ ] Remaining intentional gaps are documented as follow-ups rather than silently
  treated as complete.

## 11. Out of scope

This epic does not authorize new library capabilities, API redesigns, changed
reasoning semantics, automatic native adoption, new backend guarantees or deletion
of canonical benchmark/design evidence. Defects discovered while making examples
runnable should receive focused implementation issues when they cannot be resolved
as documentation corrections.

## 12. Implementation ledger

### Documentation example inventory and CI (implemented, pending merge)

DOC-09 #489 adds a stdlib-only Python/Cypher source-block inventory for every
Sphinx page, README/EXAMPLES/AGENTS and packaged agent Markdown: 246 examples,
including 144 standalone, 100 illustrative fragments and two notebook
continuations. Page-level ordered code digests require explicit review after
example changes; there is no automatic classification/update path. The catalog
drives rendered RST execution/install labels before capability-qualified tabs.
Standalone rows execute independently (96 direct programs plus 48 owned by the
existing tab matrix); shared-state fragments compile without claiming full setup.
All 99 direct/literal embedded Cypher queries receive parser coverage, while
complete database workflows retain actual query execution. Local Markdown links
and packaged-resource/navigation checks stay offline; strict Sphinx covers RST.

The dedicated CI lanes use Python 3.10/docs for base examples and strict Sphinx,
and Python 3.13 with only the relevant optional backends/codecs/Arrow/Polars/NetworkX
for complete example/tab coverage. Existing Python 3.12 Pages and installed-wheel
checks remain distinct. Documentation/config/catalog/test changes trigger the
gates, not just runtime edits. Contributor scope/limits live outside Sphinx in
`planning/documentation-review/example-checks.md`.

The inventory exposed and repaired a missing sampling import, two query fragments
missing required RETURN clauses, an uncaught expected profile rejection and a
fixed-path proof SVG example. Shared Markdown graph fragments are explicitly
labelled rather than pretending to reuse already closed temporary graphs.
Focused validation passes 846 tests; Ruff, inventory and strict Sphinx pass.
Delivery remains pending final full-suite/browser/CI validation and merge;
cross-audience/package acceptance remains DOC-10.

### Advanced reasoning integration (delivered)

DOC-08 #488 adds `reasoning-workflows.rst`: a task decision table and grouped
navigation for source/answer adapters, maintenance/session state, consumption and
publication, execution/representation contracts, explicit alternatives and
qualified evidence. The flat advanced landing list now hands off to this hub.
All DOC-08 pages have an intentional role and inbound tree path; formerly orphaned
contracts join the appropriate groups without URL changes. Ordinary extraction
and indexed Python remain defaults; native point sources and experimental
Polars/EL remain explicit alternatives, not adoption or performance promises.

The dispatcher entry and deletion/rebuild comparison are independently runnable,
backend-free examples. Extension-policy denial is caught as an expected failure;
catalog/maintenance/expansion fragments identify their application-owned setup.
Executor timing duplication is replaced by links to the existing detailed Polars,
plan and pipeline evidence. Stored rules, source/state identity, authentication,
closure certification, read-only consumers and guarded publication stay distinct.
Focused documentation/example validation passes 616 tests. Inventory: 106
documents / 91 Sphinx pages; backend/serializer declarations remain unchanged.
Delivered through #512 at `d39ee15` after all PR checks passed, including Python
3.10/3.13 full suites, installed artifacts and ontology conformance. The unchanged
local checkout passes 5,032 tests with 51 skips. Base Python 3.10 checks pass 13
tests; strict Sphinx passes on Python 3.10/Sphinx 8 and the local toolchain.
Browser checks cover 28 pages at three widths, 78 table checks, 327 numbered
blocks and 81 clipboard comparisons. Docs/API drift, eight agent examples,
module-size and generated figures pass. #488 is closed and #480 records ten
delivered slices; DOC-09, DOC-13 and final DOC-10 acceptance remain open, together
with graphics debt #495.

### Temporal range-reasoning journey (delivered)

DOC-07 #487 puts a complete gap-preserving range example and compact change-point
timeline before physical execution details. It compares point answers at boundaries
and inside segments against fresh evaluation, inspects per-segment evidence and
runs the conformance gate without claiming source authentication. The separate
`reasoning-range-tutorial.rst` defines independent scoped-absence/update/proof-budget,
aggregate and equality-scope examples. Computation, conformance, authenticated
finite-range admission and computation-owned publication are distinct stages;
raw caller-derived staging retains its weaker contract. Exact references remain
available with fragment scope explicit. Historical unbound intro measurements
are retained outside Sphinx, not relabelled as current-wheel results.

Focused validation passes 636 tests and strict Sphinx. The new code examples are
storage-independent and need no backend/serializer tabs; existing applicable
variants remain tested. Inventory: 105 documents / 90 Sphinx pages. Final audience
acceptance remains DOC-10. Delivered through #510 at `f958751` after all PR checks
passed, including Python 3.10/3.13 full suites, installed artifacts and ontology
conformance. The fixed-checkout local suite passes 5,028 tests with 51 skips;
base-install Python 3.10 examples pass ten tests. Strict Sphinx passes on Python
3.10/Sphinx 8 and the local toolchain. Browser checks cover 27 pages at three
widths, 324 numbered code blocks and 78 clipboard comparisons. Docs/API drift,
eight agent examples, module-size and generated-figure checks pass.

Nine slices are delivered: DOC-01–DOC-07, DOC-11 and DOC-12. Advanced integration
organization (DOC-08), documentation CI (DOC-09), packaged/final audience acceptance
(DOC-10) and the remaining platform review (DOC-13) remain open, as does graphics
debt #495. Passing validation does not complete those remaining acceptance tasks.

### Ontology and standalone Datalog foundations (delivered)

DOC-06 #486 places ontology classification/axiom origins before optional RDF and
database adapters, with classification/validation/hierarchy boundaries visible.
The existing facts/rules introduction adds a binary-versus-typed choice table;
`reasoning-typed-tutorial.rst` supplies four independent base-install examples for
comparisons, recursion, scoped absence/proofs and grouped aggregation. Exact
normalization/predicate-domain/proof contracts remain in focused references.
Navigation and the packaged ontology topic route to the progressive examples;
source-independent evaluation stays distinct from stored claim rules and source
admission/publication. Focused validation passes 612 tests and strict Sphinx.
Inventory: 104 documents / 89 Sphinx pages; backend/codec declarations remain
unchanged because these new examples do not open a database or select a codec.
Broader ontology/typed regression passes 351 tests with four optional skips;
the base-only Python 3.10 example run passes seven tests. Delivered through #509
at `dc9261a` after all PR checks passed, including Python 3.10/3.13 suites and
ontology conformance. Final audience acceptance remains DOC-10.

### Claims, stored rules and modal learning path (delivered)

DOC-05 #485 adds `claims-introduction.rst` and `stored-rules.rst`, and makes the
modal guide's first example independently runnable. The journey starts with
sourced polarity, four-valued status and confidence-as-metadata; it then derives,
explains, retracts and maintains a persisted conclusion. A second workflow tests
independent supports, and modal setup tests all four modes plus unknown without
an accessibility link under one bounded authenticated view. Scoped absence,
standalone Datalog, current graph records and audit/proof verification remain
explicitly distinct. Three setups add 36 executable alternatives: current total
50 declarations / 493 variants (469 executable, 24 compiled shared-state variants).
Inventory: 103 documents / 88 Sphinx pages. Focused validation passes 691 tests;
strict Sphinx passes on Python 3.10/Sphinx 8 and the current local toolchain.
After syncing concurrent main changes, the complete Python 3.12 suite passed
5,002 tests (60 optional skips); browser checks covered 24 pages at three widths.
Merged through #507 at `d443caf` after all PR checks passed, including Python
3.10/3.13 full suites, installed artifacts, documentation and optional integrations.

### Main integration checkpoint

DOC-12 #492 merged through #501 at `0025bbf`. The follow-through stack #502–#506
merged through consolidated #506 at `8510882`, with its complete source history
intact. DOC-03 #483 and DOC-04 #484 are delivered and closed. The superseded
stacked PRs #502–#505 are closed after verifying their heads are ancestors of main.
Rendering tests requiring docutils explicitly skip without the docs extra so
the base-install test contract remains independent of Sphinx dependencies.

### Temporal/bitemporal learning path (delivered)

DOC-04 #484 adds `temporal-introduction.rst`, `temporal-updates.rst` and
`temporal-reads.rst`. A compact Alice department timeline compares two valid
instants under two captured recorded horizons while demonstrating that current
records remain separate. Independent workflows cover partial-interval correction
and retraction, deferred-index rebuilds, authenticated pinned traversal/snapshot
materialization, and explicitly horizon-qualified Cypher. Window sampling and
interval inference are routed to their distinct APIs; recovery/migration remain
in operations. The old temporal URL and detailed service contracts are retained
as reference, with fragment scope stated and the mixed implicit-setup sketch
replaced by focused routes. README, architecture, quickstart and packaged temporal
guidance link to the new path. No runtime behavior changes.

Four database setups add 48 executable backend/serializer variants, bringing the
catalog to 47 declarations / 457 alternatives (433 executable, 24 compiled
shared-state variants). Inventory: 101 documents / 86 Sphinx pages. Validation
includes 770 documentation/temporal tests and a broader 675-test temporal,
migration, interface, Cypher and sampling run; strict Sphinx, documentation checks,
lint and offline browser checks pass. #484 is delivered through #506 at `8510882`;
final cross-audience acceptance remains DOC-10.

### Sampling learning path (delivered)

DOC-03 #483 adds `sampling-introduction.rst` with a live/snapshot decision table,
ID domains before code, and a pinned property-filtered database-to-snapshot draw
that runs after the database closes. Four independent task guides cover
neighborhoods/message frames, induced/packed subgraphs, temporal point/window
boundaries and training-negative rejection. The walk guide starts with policy
selection; hierarchy and compatibility details remain reachable in the explicitly
labelled sampling reference. README, landing navigation and the packaged sampling
topic route to this path. No runtime behavior changes.

Two database setups add 20 tested capability-qualified alternatives. The current
tab catalog has 43 declarations / 409 alternatives (385 executable and 24 compiled
shared-state fragments). The page inventory is 98 documents / 83 Sphinx pages.
Focused sampling, example, editorial, strict Sphinx and offline layout/interaction
checks validate this implementation; final cross-audience acceptance remains
DOC-10. #483 is delivered through #505/#506 at `8510882`.

### Reconciled delivery status (2026-11-03)

Publication quality is a required acceptance condition: guides describe current
library contracts, not intermediate implementation work, roadmap slices, regression
resolution or release logistics. The library-focused editorial pass removes phase
headings and stale prerelease advice, updates range/equality/publication boundaries,
and separates dense development reports into `planning/documentation-review/history/`.
Qualified reproducible benchmark evidence remains available without adoption diaries.
An editorial regression check covers Sphinx, README, examples and packaged guides;
it supplements, not replaces, semantic review. This work is delivered through
#506 at `8510882` and does not close the broader epic slices.
The fixed performance setup is now in the archived report rather than the public
guide, leaving 41 tab declarations at that checkpoint and 389 rendered alternatives
(365 executable, 24 compiled shared-state fragments).

- **Merged/delivered:** DOC-01 #481 (PR #496), DOC-02 #482 (PR #498),
  DOC-11 #491 (PR #499), consolidated by #494 at `69fe552`. Their child
  issues are closed and #480's corresponding delivery entries are checked.
- **Also delivered:** DOC-12 #492 in #501; terminology, code controls,
  generated figures, glossary tooltips, backend/serializer tabs and editorial
  cleanup through #502–#506; DOC-03 #483 and DOC-04 #484 through #505/#506.
- **Also delivered:** DOC-05 #485 through #507 at `d443caf`.
- **Further work:** DOC-06–DOC-10 and DOC-13 retain remaining scope, including
  final audience acceptance and packaged-doc alignment. Graphics debt remains
  in #495.

The existing Concepts and Terminology glossary remains canonical, expanded for
encountered jargon and linked with accessible definition tooltips. The initial
explicit declarations covered 42 database/codec setups: 389 alternatives,
including 365 executable alternatives and 24 compiled setup-fragment variants.
One fixed benchmark setup is classified without tabs. Backend-free snippets and
existing-graph continuations keep their context. Capability-qualified tabs do not
change the ordinary extraction default, deferred automatic native adoption or
optional HSA-05 #458.

DOC-01's initial implementation lives under `planning/documentation-review/`:

- `capabilities.json` maps 44 capability families to representative source/tests,
  existing coverage, delivery owners and specific remediation gaps.
- `documents.json` classifies all 71 current Sphinx pages, 11 packaged Markdown
  resources and four cross-surface entry points (86 files total).
- `check_inventory.py` checks classifications, citations and the 10-topic packaged
  registry without importing optional library dependencies. Regression tests cover
  new pages/resources, missing citations, invalid status, duplicate classifications
  and topic drift. File validation is not semantic completeness certification.
- `authoring-contract.md` establishes terminology, example execution classes and
  mathematical/pseudocode conventions: directed multigraphs, eligibility before
  sampling, Datalog fixpoints, interval intersection/union and four-valued evidence.
- `graphics.md` records 13 concrete teaching opportunities under
  [Documentation Debt: Graphics #495](https://github.com/mylonasc/gestaltdb/issues/495).
  Static accessible diagrams come first; optional animation must have a readable
  fallback, reduced-motion support and explicit controls.

These inventories are the foundation for the guide-writing slices, not a claim
that the listed documentation gaps have already been repaired. DOC-10 must
reconcile them against the final user journeys. Optional graphics may remain
explicit follow-ups rather than blocking useful prose and runnable examples.

DOC-02's initial reader-facing pass adds goal-oriented routes to README and the
Sphinx landing page, separates navigation into ten groups, and replaces the
quickstart's implicit shared-state snippets with one tested disposable-database
workflow. The example covers explicit backend/codec choice, indexes, parameterized
Cypher, snapshot sampling after the database closes, and manifest-backed reopen.
It introduces directed-multigraph notation with a plain-language reading. The
new route checks guard major capability links and the quickstart executes in a
fresh directory. Deeper introductions and advanced-page grouping remain owned by
their capability slices; linking an existing page does not repair its content.

DOC-11's initial core/persistence pass introduces `docs/property-graph.rst`,
rewrites backend/serializer guides around independent choices and complete
workflows, and preserves old storage measurements in
`docs/storage-backend-evidence.rst`. It distinguishes handle lifetime from
transactions, live readers from pinned views, current graph from temporal
history, and supported runtime capabilities from universal backend parity.
Examples explicitly exercise replacement/deletion, manifests, direct construction,
rollback, snapshot isolation, binary values and recursive temporal scalars.
The page inventory grows to 88 files (73 Sphinx pages); the original review counts
above remain the historical baseline.

DOC-12's initial pass adds `data-access.rst`, `ingestion.rst` and
`cypher-tutorial.rst`: access-path decisions, an indexed cohort, three explicit
columnar loading policies/workflows, and complete Cypher read/aggregate/write/
history examples. The syntax reference retains detailed contracts and labels
setup-dependent snippets as fragments. A stale temporal-constructor limitation
is corrected against the existing temporal-expression tests. The inventory now
contains 91 files (76 Sphinx pages).

Existing architecture graphics are retained: `components.svg` and its editable
DOT source remain the overview, with new task-guide links and a reuse/extension
policy in the graphics backlog. Layout work wraps table cells and long API names,
widens the reading column, and progressively stacks simple tables with actual
column labels on narrow screens. Complex tables keep their semantic table form.
`scripts/check_docs_layout.py` provides an optional authoring-time browser check;
it is not a new package/runtime dependency or a claim to replace a full
accessibility audit.

The terminology/usability follow-through adds a shared glossary and a first
facts/rules/explanations tutorial. Specialist pages introduce unfamiliar terms
before their examples and link to that foundation. In particular, a witness is
defined as one recorded derivation, distinguished from all independent supports
and from a source-authenticated proof certificate. The short temporal-reasoning
example now constructs its own program and imports rather than relying on hidden
setup. The inventory grows to 93 documents (78 Sphinx pages).

Code blocks receive Sphinx-rendered line numbers plus accessible copy controls
and per-page block numbers. Clipboard copying excludes line-number markup. Three
basic static figures are generated through `gestaltdb.viz.api` from the quickstart,
Cypher and rule-explanation fixtures; regeneration is checked without optional
backends. The existing architecture SVG/DOT remains the component map.
