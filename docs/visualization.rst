Explore graphs and reasoning offline
======================================

GestaltDB turns selected graph records, query results, samples and reasoning
explanations into offline interactive HTML or static SVG. The viewer is packaged
with the base library: **no JavaScript toolchain, CDN, server or network access**
is required to render it. A database-backed example still needs an installed
backend; explicit collections and standalone reasoning figures do not.

.. list-table:: Choose the data to show before rendering
   :header-rows: 1

   * - Input
     - Entry point
     - Boundary
   * - Nodes and edges already in memory
     - ``visualize_nodes_edges``
     - Base install; caller selects the records
   * - Current graph query or live neighborhood
     - ``graph.visualize(cypher=...)`` or ``graph.visualize(seeds=..., pattern=...)``
     - Explicit backend; query/sample before displaying a large graph
   * - Array-native sampling batch
     - ``visualize_sampler_batch(batch, snapshot)``
     - Requires the batch's exact snapshot for ID conversion
   * - Claim, proof, audit, supports or ontology
     - The corresponding ``visualize_*`` builder
     - Displayed evidence is not an independent proof or truth certificate

Query and sample a small graph
-------------------------------

This disposable example renders the same two-person graph from a query and a
typed neighborhood sample. HTML files include their data and viewer; SVG is
generated directly from the Python figure without a browser. Replace the
temporary output directory with your own destination to retain an artifact.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.viz.api import VizOptions

   with TemporaryDirectory() as directory:
       with GraphDB.create(f"{directory}/db", backend="leveldb", serializer="pickle") as graph:
           graph.put_nodes([
               Node("alice", labels=["Person"], properties={"name": "Alice"}),
               Node("bob", labels=["Person"], properties={"name": "Bob"}),
           ])
           graph.put_edge(Edge("e1", "alice", "bob", {"type": "knows"}))
           options = VizOptions(node_label_property="name", edge_labels="all")
           figure = graph.visualize(
               'MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b', options=options,
           )
           sampled = graph.visualize(
               seeds=["alice"], pattern=[{"edge_type": "knows", "sample_size": 5}], options=options,
           )
           assert figure.viz.node_ids == ("alice", "bob")
           assert sampled.viz.edge_ids == ("e1",)
       # Figures contain their selected payload; the database can now be closed.
       html_path = figure.save(Path(directory) / "knows.html")
       svg_path = figure.save_svg(Path(directory) / "knows.svg", layout="circle")
       sampled.save(Path(directory) / "neighborhood.html")
       assert "Alice" in html_path.read_text() and "<svg" in svg_path.read_text()

Inspect a standalone reasoning witness
---------------------------------------

An ontology can derive that Alice is a Person from her employment relation.
A :term:`witness` records one route to that conclusion. This **backend-free**
example renders the witness and the ontology separately; it does not write
claims, merge identifiers or certify the supplied employment fact.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.ontology import Domain, Ontology, TYPE_PREDICATE, compile_ontology
   from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
   from gestaltdb.viz.api import VizOptions, visualize_ontology, visualize_proof_explanation

   ontology = Ontology("people", [Domain("worksFor", "Person")])
   compiled = compile_ontology(ontology)
   result = DatalogEngine().evaluate(
       compiled.program, [Fact("alice", "worksFor", "lab")],
       limits=ReasoningLimits(), collect_proofs=True,
   )
   explanation = result.explain(Fact("alice", TYPE_PREDICATE, "Person"))
   assert not explanation.truncated
   options = VizOptions(node_label_property="label", edge_labels="all")
   proof = visualize_proof_explanation(explanation, options=options)
   schema = visualize_ontology(ontology, options=options)
   with TemporaryDirectory() as directory:
       proof.save(Path(directory) / "witness.html")
       svg_path = proof.save_svg(Path(directory) / "witness.svg")
       schema.save_svg(Path(directory) / "ontology.svg")
       assert "alice" in svg_path.read_text()

The generated witness figure in :doc:`reasoning-introduction` uses these same
public APIs. For historical claim explanations and model-output audits, follow
:doc:`claims-introduction` and :doc:`inference-audit`. Exact builder directions,
shapes and all-support versus first-witness display are in
:doc:`visualization-reference`.

Choose an output, not an implied synchronization mode
-----------------------------------------------------

- **HTML / Jupyter:** ``save`` writes a self-contained interactive artifact;
  displaying ``figure`` in a notebook embeds its HTML in a height-bounded iframe.
  It does not query the database again or synchronize changes with Python.
- **Python SVG:** ``save_svg`` uses deterministic layouts or supplied coordinates.
  It does not capture browser dragging, filters, selection or zoom. Reasoning
  figures suggest a layered tree; ordinary graph figures default to a circle.
- **Browser SVG/PNG:** export the current viewport or all visible content with
  the active browser styling/filters. Direct Python PNG rasterization is not
  provided. Browser export is not a replacement for the underlying graph data.
- **View-state JSON:** restores positions, pins, settings and simulation state
  only for the same embedded topology. It is separate from graph JSON; loading
  it neither loads new records nor contacts a notebook kernel or database.

Figures cap displayed topology (defaults 2,000 nodes / 5,000 edges) and warn on
deterministic truncation; exceeding absolute ceilings raises. Use a bounded query
or sample for large graphs rather than assuming rendering is out-of-core.
Artifacts embed selected data/properties: browser hiding is **not access control**.
Select only data you intend to distribute before saving the artifact.

Continue with :doc:`notebooks` for kernel/lifetime setup,
:doc:`networkx` for materialized graph exchange and :doc:`performance` for
separate extraction, layout/export and application costs.

.. toctree::
   :maxdepth: 1

   visualization-reference
