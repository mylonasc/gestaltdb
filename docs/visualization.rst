Visualization
=============

GestaltDB ships interactive HTML/JS graph visualization with the core
library. The front end (D3.js + React) is prebuilt at library-dev time and
packaged with the Python distribution, so rendering artifacts needs **no
JavaScript toolchain, no network access, and no new runtime dependency**.

Quick Start
-----------

.. code-block:: python

   from tempfile import TemporaryDirectory

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import PickleSerializer

   with TemporaryDirectory() as tmpdir:
       graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
       try:
           graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
           graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
           graph.put_edge(Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows"}))

           figure = graph.visualize('MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b')
           figure.save("/tmp/knows.html")  # open in any browser, offline
       finally:
           graph.close()

In Jupyter, the last line can simply be ``figure``: ``VizFigure`` renders
inline through ``_repr_html_`` (a height-bounded ``srcdoc`` iframe).

Entry Points
------------

.. code-block:: python

   from gestaltdb.viz.api import VizOptions, visualize_nodes_edges, visualize_query, visualize_sample

   # Explicit collections.
   figure = visualize_nodes_edges(nodes, edges)

   # Cypher matches (matched entities are highlighted in the canvas).
   figure = visualize_query(graph, 'MATCH (a:Person) RETURN a LIMIT 25')

   # Typed sampling around seeds (the large-graph path).
   from gestaltdb.sampling import SamplingHop, SamplingPattern
   pattern = SamplingPattern([SamplingHop("binds", direction="out", sample_size=5)])
   figure = visualize_sample(graph, ["drug-1"], pattern)

   # ML training batches mapped back to external IDs.
   from gestaltdb.viz.api import visualize_sampler_batch
   figure = visualize_sampler_batch(batch, snapshot)

Options (``VizOptions``) cover node/edge caps (defaults 2000/5000),
absolute ceilings (default twice the caps, raising
``VizCapExceededError`` with sampling guidance), iframe height, light/dark
theme, force-layout charge and link distance, artifact title, and
label/property visibility. Any deterministic truncation warns with
``TruncationWarning`` and is bannered in the canvas with exact counts.

Canvas Features
---------------

- D3 force layout with drag-to-pin, double-click release, pan/zoom, refit,
  pause, and tunable charge/link distance.
- Deterministic colorblind-safe coloring by node group and edge type, with
  a count legend that doubles as a visibility filter.
- Click/keyboard selection with a properties inspector, hover tooltips,
  search by id/label, 1-hop neighborhood focus, and a highlight overlay
  for Cypher matches (toggleable).
- Client-side export: SVG snapshot, PNG raster, and JSON payload download.
  Everything runs locally inside the artifact.

Offline Guarantee
-----------------

Artifacts are single self-contained ``.html`` files: the JS/CSS bundle and
the JSON payload are inlined, there are no CDN or font requests, and the
markup carries an inline-only Content Security Policy. Opening a saved
file over ``file://`` with networking disabled renders fully.

Large Graphs
------------

Never dump a whole 100k-node database into the DOM: payloads beyond the
absolute ceiling raise an actionable error pointing at
``visualize_sample()`` and ``LIMIT``-bounded ``visualize_query()``. Caps
keep the first IDs in sorted order, so repeated builds are byte-identical
and truncation counts are exact.

Front-End Development
---------------------

The React + TypeScript + Vite source lives in ``web/`` and builds into
``src/gestaltdb/viz/static/`` (committed). ``npm run build`` reproduces the
bundle byte-identically (modulo the manifest timestamp);
``npm run check:licenses`` gates every dependency against a permissive
allowlist (MIT/ISC/Apache-2.0/BSD/CC0). Python tests never need npm.
