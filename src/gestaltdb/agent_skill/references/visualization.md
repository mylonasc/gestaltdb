# Visualization For Library Users

GestaltDB ships offline interactive visualization with the core library: the
D3.js + React front end is prebuilt and packaged, so saving `.html`
artifacts or rendering in Jupyter needs no JavaScript toolchain, no
network, and no new runtime dependency.

Entry points (import from `gestaltdb.viz.api`, not the package root):

- `visualize_nodes_edges(nodes, edges)` for explicit collections.
- `visualize_query(graph, cypher, parameters=None)` for Cypher matches
  (matched entities are highlighted in the canvas).
- `visualize_sample(graph, seeds, pattern)` for typed sampling around seeds
  (the large-graph path).
- `visualize_sampler_batch(batch, snapshot)` for ML batches mapped back to
  external IDs via `node_ids_global`.
- `visualize_claim_explanation(explanation)` for `graph.rules.explain`
  derivation graphs (claim/rule/absence nodes, root highlighted).
- `visualize_inference_audit(report)` for `export_inference` audits as a
  mapping or `InferenceExplanation` (no live database needed).
- `visualize_proof_explanation(explanation)` for standalone Datalog proof
  witnesses (`ProofTrace.explain` / `EvaluationResult.explain`) as fact trees.
- `visualize_support_set(fact, supports, witness=None)` for all alternative
  typed justifications with the chosen `RankedWitness` highlighted.
- `visualize_ontology(ontology)` for class/property hierarchies (binary
  axioms as typed edges; chains/intersections as explicit axiom nodes).
- `visualize_inference_chain(nodes, edges, explanation, ontology)` for the
  whole chain in one figure: graph + proof/claim explanation + ontology,
  linked by exact ID equality (`mentions`/`typed_as`) unless
  `link_layers=False`.
- `graph.visualize(cypher=...)` or `graph.visualize(seeds=..., pattern=...)`
  as a `GraphDB` method.
- `VizOptions` for caps (defaults 2000 nodes / 5000 edges), absolute
  ceilings (default twice the caps), iframe height, light/dark theme,
  force-layout charge/link distance, title, and label/property visibility.
  `node_label_property="name"` uses a readable property with ID fallback;
  `edge_label_property` selects an edge property with type/ID fallback.
  `edge_labels="off"|"selected"|"all"` controls relationship-label visibility.
  The default `"selected"` also shows hovered and keyboard-focused edge labels.
  `label_max_length` limits displayed labels (default 40 characters).

Every builder returns a `VizFigure` with `save(path)`, `as_html()`, and
Jupyter `_repr_html_()`. Caps truncate deterministically with a
`TruncationWarning` and an on-canvas banner; payloads beyond the absolute
ceiling raise `VizCapExceededError` naming the sampling alternative.
Reasoning builders accept the same caps and record `meta["builder"]`
(`from_claim_explanation`, `from_inference_audit`,
`from_proof_explanation`, `from_support_set`, `from_ontology`,
`from_inference_chain`) plus `meta["suggested_layout"] = "tree"`. Every
reasoning node carries a readable `label` property, so
`VizOptions(node_label_property="label", edge_labels="all")` is the
recommended setting for HTML and static SVG alike. Shapes encode the layer:
circles for data, triangles for inferred conclusions, squares for schema;
dotted edges mark inference steps. The viewer opens reasoning artifacts
pre-arranged (and pinned) as a layered tree; the Tree layout button
re-applies it and Reset layout returns to force-directed.

`figure.as_svg()` / `figure.save_svg(path)` produce dependency-free static SVG
with `layout="circle"` (default for graph figures), `layout="grid"`,
`layout="tree"` (layered top-down; the default for reasoning figures), or
supplied `positions={node_id: (x, y)}`. Coordinates must cover every node and are fitted
uniformly to `width`/`height`. Static SVG renders Python data/options, not the
interactive iframe state. Use `edge_labels="all"` for static relationship labels.
Browser SVG/PNG exports capture the current viewport or all visible content,
with themed/transparent backgrounds, image width/height, and PNG scale (1–4).
Dimensions must be integers from 1 to 8192; PNG output is limited to 32 megapixels.
Exports preserve active label/style/filter/selection settings. Graph JSON is a
separate data export. Python PNG rasterization is not provided.

## Exploration and appearance

Search includes property values by default and can be restricted to IDs/labels.
Native-label filtering includes secondary labels. Neighborhood focus supports
incoming/outgoing/both traversal and 1–5 hops over enabled relationship types
and node filters. The visible-entity selector supports keyboard selection;
selected nodes can be explicitly pinned/unpinned. Reset layout restores the full
simulation; relayout-visible excludes hidden topology until another relayout.

`VizOptions(node_size_property="mass", node_color_property="kind",
edge_width_property="score", edge_color_property="category")` configures
attribute mappings in both the browser and Python SVG output. Numeric node
radii map to 4–24 and edge widths to 0.5–6 over the full payload's range.
Missing/non-numeric values use 7 and 1.2; constant numeric values map to the
midpoint. Colors use categorical values with native-group/type fallback.
The browser exposes these mappings under Labels and attribute styling.
Reserved metadata such as `score` remains available to label/style selectors and
the inspector. Parallel/reciprocal edges and self-loops have distinct directed
paths and independent pointer/keyboard selection targets.

Save/load view state is a separate versioned JSON artifact containing positions,
pins, zoom, settings, selection, filters, and active simulation membership.
Loading validates the whole artifact against the embedded graph topology
before applying it. It does not load new graph data or contact the database.
Coordinates can be reused with Python SVG by passing
`positions={n["id"]: (n["x"], n["y"]) for n in state["layout"]["nodes"]}`.
This reuses coordinates, not the browser viewport or filters.
Loading a paused view restores a fixed arrangement; an unpaused view resumes
simulation from saved coordinates. The local file limit is 16 MB. Each notebook
iframe is independent: browser changes do not update the Python `VizFigure`.

Full runtime dependency license/notice texts are embedded in offline HTML and
can be saved with Download third-party notices. No CDN, remote fonts, server,
or notebook-kernel communication is required by the viewer.

## Example: explicit collections, no backend required

This example works with a base GestaltDB installation and does not open a
database. HTML and SVG use the same property label/style configuration.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, Node
from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

nodes = [
    Node("drug-1", labels=["Drug", "Compound"],
         properties={"name": "Aspirin", "score": 0.9, "family": "compound"}),
    Node("protein-1", labels=["Protein", "Target"],
         properties={"name": "COX-1", "score": 0.5, "family": "enzyme"}),
]
edges = [
    Edge("binds", "drug-1", "protein-1",
         properties={"type": "binds", "confidence": 0.95, "status": "reviewed"}),
    Edge("inhibits", "drug-1", "protein-1",
         properties={"type": "inhibits", "confidence": 0.7, "status": "candidate"}),
    Edge("self", "protein-1", "protein-1",
         properties={"type": "interacts", "confidence": 0.3, "status": "candidate"}),
]
figure = visualize_nodes_edges(nodes, edges, options=VizOptions(
    node_label_property="name", edge_labels="all",
    node_size_property="score", node_color_property="family",
    edge_width_property="confidence", edge_color_property="status",
))
positions = {"drug-1": (-1, 0), "protein-1": (1, 0)}
with TemporaryDirectory() as tmpdir:
    figure.save(Path(tmpdir) / "graph.html")
    svg_path = figure.save_svg(Path(tmpdir) / "graph.svg", positions=positions,
                               width=1000, height=600, background=False)
    assert svg_path.read_text() == figure.as_svg(
        positions=positions, width=1000, height=600, background=False)
    grid_svg = figure.as_svg(layout="grid")
    assert grid_svg.startswith("<svg")
```

## Example: visualize a database query

Only graph entities projected by the query become topology; scalar-only
projections cannot produce a graph. Query figures highlight their returned
entities, rather than automatically fetching a larger background graph.
This example requires the `leveldb` extra and closes the database explicitly.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import VizOptions, visualize_query

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node("alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node("bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge("e1", "alice", "bob", properties={"type": "KNOWS"}))

        figure = visualize_query(
            graph,
            "MATCH (a:Person)-[r:KNOWS]->(b) RETURN a, r, b",
            options=VizOptions(title="Knows graph", node_label_property="name", edge_labels="all"),
        )
        figure.save(f"{tmpdir}/knows.html")
        figure.save_svg(f"{tmpdir}/knows.svg")
        assert figure.viz.node_ids == ("alice", "bob")
    finally:
        graph.close()
```

## Example: visualize a proof trace (no backend required)

```python
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Atom, Fact, Program, ReasoningLimits, Rule, Variable
from gestaltdb.viz.api import VizOptions, visualize_proof_explanation

x, y = Variable("X"), Variable("Y")
program = Program([Rule("parent-ancestor", [Atom(x, "parent", y)], Atom(x, "ancestor", y))])
result = DatalogEngine().evaluate(
    program, [Fact("a", "parent", "b")], limits=ReasoningLimits(), collect_proofs=True)
figure = visualize_proof_explanation(
    result.explain(Fact("a", "ancestor", "b")),
    options=VizOptions(node_label_property="label", edge_labels="all"),
)
assert "a ancestor b" in figure.as_svg()
```
