# Outline coverage

Tracked in [GitHub issue #273](https://github.com/mylonasc/gestaltdb/issues/273).
Unchecked rows are full-outline acceptance items, not claims that no draft
exists. A Sphinx build is not semantic or executable-example verification.

Checklist for the `Gestalt-graph-onto-tutorial` outline. Updated per commit;
each row names the doc file, the epic/issue anchor, and the verification.

- [ ] ch01 property-graph vs semantic — #156 context, `graphdb.py`, `ontology/adapters/rdf.py` — `ch01-property-graph-vs-semantic.rst`
- [ ] ch02 fragment + compiler — `ontology/model.py`, `ontology/compiler.py`, gated profiles #184 — `ch02-ontology-fragment.rst`
- [ ] ch03 Datalog IRs — `reasoning/model.py`, `relational.py`, `batches.py`, `plans.py`, `executors.py`, RE-01..RE-06 (#162–#167) — `ch03-datalog-irs.rst`
- [ ] ch04 storage grounding — `extraction.py`, `adapters/batched.py`, `native.py` (#194), `adapters/graph.py` — `ch04-storage-grounding.rst`
- [ ] ch05 claims/temporal/modal — `versioning.py`, `temporal.py`, `epistemic.py`, `modal.py`, RE-E4 (#160) — `ch05-claims-temporal.rst`
- [ ] ch06 query/sampling — Cypher, `ontology-graph.rst`, `SamplerSnapshot/Engine`, #255/#256 — `ch06-query-sampling.rst`
- [ ] ch07 performance — RE-03/04/05/18/22 evidence, cooperative cancel — `ch07-performance.rst`
- [ ] ch08 gaps — #194, #200, #248–#254, RE-E5 (#161), PropEnh #124–#128 — `ch08-gaps.rst`
- [ ] toolkit (figures/equations/snippets/viz) — `viz/api.py` caps, `myst dollarmath`, `mathjax` — `toolkit/`
- [ ] notebooks nb01–nb03 — `pytest tests/test_ontology_tutorial.py`, `sphinx -W` — `notebooks/`

## Review corrections (2026-10-22)

- [x] Initial drafts committed and pushed before review.
- [x] Distinguish cross-technology comparisons from supported GestaltDB endpoints.
- [x] Seed closure math with input assertions; account for unions of temporal supports.
- [x] Separate available implementations, experimental contracts, and active acceptance work.
- [x] Document setup, expected outcomes, temporary stores, and notebook downloads.
- [x] Add tests executing chapter code and notebook cells, and reproducing all figures.

## Graphics and PDF (2026-10-22)

- [x] Replace tiny circles with large rounded, padded nodes and bold wrapped names.
- [x] Use automatic layered layout, crossing reduction, conceptual clusters, and curved splines.
- [x] Preserve GestaltDB node/edge data and engine-computed memberships; distinguish inferred edges.
- [x] Add a full-resolution overview download and selectable vector text.
- [x] Add `build_docs.sh --pdf` for a tutorial-only XeLaTeX PDF with vector diagrams.
- [x] Document optional build dependencies and test flag handling and missing-tool errors.

## Reader and reasoning-engine pass (2026-10-22)

- [x] Begin with an application problem and motivate the graph, storage, vocabulary, and inference layers.
- [x] Define knowledge graphs and distinguish them from database products and RDF serialization.
- [x] Teach rule notation, substitutions, joins, safety, recursion, least fixed points, soundness, and completeness.
- [x] Explain why deltas, plans, compact IRs, demand evaluation, and support maintenance are useful.
- [x] Compare Soufflé, tabled SWI-Prolog, Clingo, and Jena using official sources and explicit semantic boundaries.
- [x] Add a continuous stored-data-to-inference example, a glossary, and explained self-checks in every chapter.
- [x] Rewrite core explanations as motivated prose rather than feature inventories.
- [x] Keep branch/build/checklist/toolkit material in HTML maintainer pages, outside the reader PDF.
- [x] Validate the PDF's assembled source excludes maintainer pages and reject missing font glyphs.

## Employment and role-model correction

- [x] Give worksFor an Employee domain; infer Person via the subclass relation.
- [x] Require separate positive job-description evidence to refine Employee into Researcher.
- [x] Distinguish explicit not_researcher categories, missing evidence, and logical negation.
- [x] Explain employment versus co-location and the fuller reified activity/job model.
- [x] Update worked code, notebooks, illustrations, and the committed reader PDF together.
