# Documentation graphics backlog

Tracking: [Documentation Debt: Graphics #495](https://github.com/mylonasc/gestaltdb/issues/495).
These are proposed teaching aids, not claims of implemented UI or runtime behavior.
The owning documentation slice should link its guide to the corresponding item.

| ID | Owner | Guide / concept | Proposed visual and learning outcome |
| --- | --- | --- | --- |
| VIS-01 | DOC-11 | Persistence and lifecycle | Static interchangeable backend/serializer diagram: graph API → independently selected store and codec. Show capability differences, not universal transaction parity. |
| VIS-02 | DOC-12 | Indexing and ingestion | Static write → maintain/defer → rebuild → fresh lookup flow; distinguish canonical records from secondary indexes. |
| VIS-03 | DOC-12 | Cypher | Small side-by-side graph, pattern and result table. Highlight matched edges and bindings; make relationship direction and path semantics explicit. |
| VIS-04 | DOC-03 | Snapshot sampling | Static three-stage graph → compact IDs/CSR → sampled batch diagram, with external/global/local ID correspondence. |
| VIS-05 | DOC-03 | Neighborhood/layer sampling | Optional step-through animation of seeds, eligible neighbors, fanout and next layer. Filtering happens before draws; distinguish graph-edge and message-passing orientation. |
| VIS-06 | DOC-03 | Walks and negatives | Optional walk-policy stepper and timeline of positive/negative eligibility. Show rejection and exhaustion without implying embedding training is built in. |
| VIS-07 | DOC-04 | Bitemporal history | Valid-time × system-time diagram with assertion, correction and finite retraction; let the reader compare old/new horizons. All intervals are half-open. |
| VIS-08 | DOC-05 | Epistemic status and modality | Four-valued support/refutation grid, plus a separate agent/world accessibility graph. Missing accessibility is unknown, not vacuous truth. |
| VIS-09 | DOC-06 | Ontology and Datalog | Axiom → compiled rule → input facts → inferred fact → witness DAG. Distinguish classification from validation and one witness from all supports. |
| VIS-10 | DOC-06 | Fixpoint and maintenance | Optional delta-round animation and independent-support deletion stepper. Deleting one support does not necessarily retract a conclusion. |
| VIS-11 | DOC-07 | Temporal closure | Aligned premise intervals, intersection, change-point segments and derived interval union. Optional scrubber compares range and point answers. |
| VIS-12 | DOC-08 | Integration lifecycle | Static source → mapping → evaluation → admission → explicit publication decision graph. Separate current versus temporal identities and read authority versus write authority. |
| VIS-13 | DOC-13 | Cost model | Static build/load/steady-state breakdown for ingestion/sampling/reasoning; show authentication separately, without fabricated timings. |

## Acceptance for each delivered visual

- Specify the exact guide, learning question, input fixture and expected result.
- Prefer a small static SVG first. Animation is optional progressive enhancement;
  all meaning must remain in text and a static fallback.
- Supply useful alt text, a caption and a readable narrow-screen presentation.
- Use shape/text as well as color; retain contrast in light/dark presentation.
- Respect reduced-motion settings; animations must have pause/step controls and
  must not autoplay essential information away.
- No network/CDN dependency is required to understand a guide or view an example.
- Derive labels and outputs from supported, tested examples. Do not depict
  unsupported executor combinations, silent inference or automatic publication.
- Record source/licensing for assets and instructions for reproducible updates.
- Close an item only after checking the rendered page, including its fallback.
