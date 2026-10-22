# Outline coverage

Tracked in GitHub issue #273 (no GitLab remote/CLI configured in this
environment; mirror there on request).

Checklist for the `Gestalt-graph-onto-tutorial` outline. Updated per commit;
each row names the doc file, the epic/issue anchor, and the verification.

- [ ] ch01 property-graph vs semantic — #156 bối cảnh, `graphdb.py`, `ontology/adapters/rdf.py` — `ch01-property-graph-vs-semantic.rst`
- [ ] ch02 fragment + compiler — `ontology/model.py`, `ontology/compiler.py`, gated profiles #184 — `ch02-ontology-fragment.rst`
- [ ] ch03 Datalog IRs — `reasoning/model.py`, `relational.py`, `batches.py`, `plans.py`, `executors.py`, RE-01..RE-06 (#162–#167) — `ch03-datalog-irs.rst`
- [ ] ch04 storage grounding — `extraction.py`, `adapters/batched.py`, `native.py` (#194), `adapters/graph.py` — `ch04-storage-grounding.rst`
- [ ] ch05 claims/temporal/modal — `versioning.py`, `temporal.py`, `epistemic.py`, `modal.py`, RE-E4 (#160) — `ch05-claims-temporal.rst`
- [ ] ch06 query/sampling — Cypher, `ontology-graph.rst`, `SamplerSnapshot/Engine`, #255/#256 — `ch06-query-sampling.rst`
- [ ] ch07 performance — RE-03/04/05/18/22 evidence, cooperative cancel — `ch07-performance.rst`
- [ ] ch08 gaps — #194, #200, #248–#254, RE-E5 (#161), PropEnh #124–#128 — `ch08-gaps.rst`
- [ ] toolkit (figures/equations/snippets/viz) — `viz/api.py` caps, `myst dollarmath`, `mathjax` — `toolkit/`
- [ ] notebooks nb01–nb03 — `doc_tool.py test-examples`, `sphinx -W` — `notebooks/`
