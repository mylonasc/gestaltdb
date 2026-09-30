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

Readable Labels
---------------

``node_label_property`` selects a property to display instead of the node ID;
missing or null values fall back to the ID. ``edge_label_property`` selects an
edge property, falling back to the relationship type (or edge ID for untyped
edges). Reserved metadata such as ``score`` is also available as a label source.
``label_max_length`` limits displayed text (default 40 characters).

``edge_labels`` accepts ``"off"``, ``"selected"`` (the default: selected,
hovered, or keyboard-focused relationships), or ``"all"``. The viewer exposes
this setting independently of node-label visibility. Parallel relationships,
reciprocal relationships, and self-loops have distinct directed paths and
independent selection targets.

Direct SVG Output
-----------------

SVG generation from Python requires no browser, JavaScript runtime, or additional
dependency. It uses a deterministic circle or grid layout, or supplied node
coordinates. Coordinates must cover every displayed node and are uniformly
scaled into the output viewport.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from pathlib import Path
   from gestaltdb.graphdb import Edge, Node
   from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

   figure = visualize_nodes_edges(
       [Node(node_id="alice", properties={"name": "Alice"}),
        Node(node_id="bob", properties={"name": "Bob"})],
       [Edge(edge_id="e1", source="alice", target="bob",
             properties={"type": "knows"})],
       options=VizOptions(node_label_property="name", edge_labels="all"),
   )
   svg = figure.as_svg(width=1200, height=800, layout="circle")
   with TemporaryDirectory() as tmpdir:
       figure.save_svg(Path(tmpdir) / "graph.svg", layout="grid", background=False)

``as_svg()`` and ``save_svg()`` render the Python payload and options, not the
current iframe's force layout, selection, filters, or manually dragged positions.
For static relationship labels, set ``edge_labels="all"``; static output has no
hover or selection state. ``positions={"alice": (0, 0), "bob": (1, 0)}`` can be
passed to either method. ``save()`` continues to write interactive HTML.
PNG output is available through the browser's Export PNG control; direct Python
PNG rasterization is not provided.

Attribute Styling
-----------------

``VizOptions`` supports ``node_size_property``, ``node_color_property``,
``edge_width_property``, and ``edge_color_property`` in interactive and static
SVG rendering. The browser also exposes these settings under Labels and
attribute styling.

Numeric node radii map to 4–24 and edge widths to 0.5–6, based on the full
payload's finite numeric values. Missing and nonnumeric values (including
booleans) use the defaults 7 and 1.2. Constant numeric values map to the midpoint.
Color properties are categorical; missing values fall back to the native node
group or relationship type. Labels and selection provide additional identity
cues when palette colors repeat.

.. code-block:: python

   options = VizOptions(
       node_label_property="name",
       node_size_property="mass",
       node_color_property="category",
       edge_width_property="score",
       edge_color_property="status",
       edge_labels="all",
   )

Image Export and Saved Views
----------------------------

The Export and saved view panel offers current-viewport or all-visible-content
SVG/PNG output, themed or transparent backgrounds, output width/height, and
PNG scale 1–4. Width/height must be integers from 1 to 8192; PNG output is limited
to 32 megapixels. SVG preserves aspect ratio, so differently shaped output
dimensions may introduce padding. Exports preserve active styling and filters.

Save view state downloads a versioned JSON file containing positions, pins,
zoom, viewer settings, selection, filters, and active simulation membership.
Load view state accepts a local file for the same embedded graph topology and
validates all fields before applying changes. No server or notebook kernel
communication is required. Loading a paused view restores the arrangement
exactly; loading an unpaused view resumes simulation from the saved positions.
Download graph JSON remains a separate data export.

To reuse saved coordinates in Python, read the view JSON and pass its positions
to ``save_svg``; this does not apply the saved viewport, selection, or filters:

.. code-block:: python

   import json
   state = json.loads(Path("gestaltdb-view.json").read_text())
   positions = {node["id"]: (node["x"], node["y"])
                for node in state["layout"]["nodes"]}
   figure.save_svg("arranged.svg", positions=positions)

Canvas Features
---------------

- D3 force layout with drag-to-pin, double-click release, pan/zoom, refit,
  pause, and tunable charge/link distance.
- Dragging also works while paused; fit-visible includes displayed geometry
  and labels. The viewer refits when its container is resized.
- Explicit pin/unpin actions and dashed pin indicators, full-layout reset, and
  visible-only relayout. Relayout-visible excludes hidden nodes/relationships
  from the simulation until another relayout or a saved view is loaded.
- Deterministic colorblind-safe coloring by node group and edge type, with
  a count legend that doubles as a visibility filter.
- Click/keyboard selection with a properties inspector, hover tooltips,
  search by id/label/property, incoming/outgoing/both neighborhood focus with
  1–5 hops over enabled relationship types and node filters, and a highlight overlay
  for Cypher matches (toggleable).
- Client-side export: SVG snapshot, PNG raster, and JSON payload download.
  SVG/PNG capture the selected image scope with resolved theme and label styling.
  JSON contains graph data, not exploration state; view state is a separate export.
  Everything runs locally inside the artifact, with export errors shown inline.
- A keyboard-friendly visible-entity selector, collapsible inspector, and
  responsive controls for narrower notebook cells. Native-label filtering
  includes secondary labels, independently of primary-group filters.

Offline Guarantee
-----------------

Artifacts are single self-contained ``.html`` files: the JS/CSS bundle and
the JSON payload are inlined, there are no CDN or font requests, and the
markup carries an inline-only Content Security Policy. Opening a saved
file over ``file://`` with networking disabled renders fully.
Full runtime dependency license/notice texts are packaged and embedded in each
HTML artifact; Download third-party notices saves them locally.

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

The manifest records resolved dependency/toolchain versions, hashes for JS/CSS
and notices, and hashes for source/config/lockfile build inputs. In a checkout,
freshness checks detect changed or added frontend source files; installed wheels
verify packaged assets without requiring a frontend source tree. License checks
retain nested package paths and versions and fail for missing dependencies.
Notices conservatively include the complete runtime dependency set, including
tree-shaken modules. ``npm run check:bundle`` rebuilds and compares asset/source
hashes against the previous manifest; the Visualization CI workflow runs this
gate and the offline browser tests.

Browser regressions use Playwright as a development-only dependency. After
``npm ci`` and ``npx playwright install chromium`` in ``web/``, run
``npm run build`` and ``npm test``. The tests generate a production HTML artifact
with Python and open it over ``file://`` with networking disabled. Set
``VIZ_TEST_PYTHON`` to the Python executable with GestaltDB's dependencies
installed, if it is not ``python3``.
