Notebook Usage
==============

Use the same public Python APIs in a notebook as in a script. Choose a :term:`notebook kernel`
with the application's backend/codec extras installed; a ``VizFigure`` displays
as an offline HTML iframe without a JavaScript build or database server. Browser
interaction does not update Python variables or write back to the database
(:doc:`visualization`).

The source repository includes optional notebooks under ``notebooks/``; the
installed library does not need a repository checkout to use the example below.

Available notebooks include:

- ``01_graphdb_basics.ipynb`` for core node and edge operations.
- ``02_loading_large_graph.ipynb`` for bulk graph loading patterns.
- ``03_property_graph_api.ipynb`` for attributed property graph usage.
- ``04_typed_path_sampling.ipynb`` for typed traversal and sampling.
- ``05_columnar_ingestion_benchmark.ipynb`` for Arrow/Polars columnar ingestion
  compared with LevelDB object batch ingestion.
- ``09_graph_visualization.ipynb`` for offline interactive visualization:
  ``visualize_*`` entrypoints, Cypher highlighting, typed sampling, caps,
  property labels/styling, selectable multigraph relationships, directed
  exploration, themes, browser SVG/PNG and saved-view exports, direct Python
  SVG layouts/coordinates, and sampler-batch inspection. It retrieves the
  visualization guide shipped with the installed library. The optional
  saved-coordinate cell skips cleanly until a local view-state file is supplied.
- ``10_sherlock_temporal_knowledge_graph.ipynb`` for extracting a temporal,
  epistemic narrative graph with a local Ollama model and explaining what
  Sherlock Holmes could know at a selected scene without later-story leakage.

The Ollama narrative notebook requires a separately configured local model
service; it is not a base-install or offline-model guarantee. Benchmark notebooks
are source tools, not latency promises for a fresh installation.

Choose the Correct Kernel
-------------------------

Use a supported Python 3.10–3.13 environment. Install Jupyter separately and
register that environment's interpreter; the notebook server and kernel may run
in different environments. From an activated environment:

.. code-block:: sh

   python -m pip install "gestaltdb[leveldb]" notebook ipykernel
   python -m ipykernel install --user --name gestaltdb --display-name "GestaltDB"

Select **GestaltDB** in the notebook's kernel picker. Other backends and codecs
remain independent choices (:doc:`installation`). Base-only reasoning, array
sampling and explicit-collection visualization need no embedded backend.

Minimal Notebook Cell
---------------------

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.viz.api import VizOptions

   with TemporaryDirectory() as directory:
       with GraphDB.create(f"{directory}/db", backend="leveldb", serializer="pickle") as graph:
           graph.put_nodes([Node("drug-1"), Node("protein-1")])
           graph.put_edge(Edge("e1", "drug-1", "protein-1", {"type": "binds"}))
           figure = graph.visualize(
               'MATCH (a)-[r:binds]->(b) RETURN a, r, b',
               options=VizOptions(edge_labels="all"),
           )
           assert figure.viz.edge_ids == ("e1",)
   figure  # Jupyter displays the detached payload after the graph is closed

Close Stores Between Runs
-------------------------

Repeated notebook execution can keep database files open. Close the graph before
rerunning setup cells that recreate stores.

The cell above uses context managers and removes its disposable storage on exit,
so rerunning it does not reuse an old handle. For a deliberately persistent
multi-cell graph, use your own path and close the handle explicitly after the
last read/write. Keeping a figure displayed does not require keeping its source
graph open. Each iframe has independent view state; saved coordinates/filters
are not automatically sent back to Python (:doc:`visualization-reference`).

Troubleshooting ``plyvel``
--------------------------

If ``LevelDBStore`` raises a missing dependency error after installing extras,
check the kernel interpreter:

.. code-block:: python

   import sys
   print(sys.executable)
   print(sys.version)

If it is Python 3.14 or a free-threaded build, switch the notebook to a Python
3.12 kernel and install ``gestaltdb[leveldb]`` there.
