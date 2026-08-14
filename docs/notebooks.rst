Notebook Usage
==============

The repository includes notebooks under ``notebooks/``. They use the same public
APIs described in this documentation.

Available notebooks include:

- ``01_graphdb_basics.ipynb`` for core node and edge operations.
- ``02_loading_large_graph.ipynb`` for bulk graph loading patterns.
- ``03_property_graph_api.ipynb`` for attributed property graph usage.
- ``04_typed_path_sampling.ipynb`` for typed traversal and sampling.
- ``05_columnar_ingestion_benchmark.ipynb`` for Arrow/Polars columnar ingestion
  compared with LevelDB object batch ingestion.

Choose the Correct Kernel
-------------------------

For LMDB-only notebooks, any supported Python version with ``lmdb`` installed is
enough. For LevelDB notebooks, use a CPython version that can install ``plyvel``.
Python 3.12 is a safe default.

.. code-block:: sh

   uv python install 3.12
   uv venv --python 3.12
   uv sync --extra leveldb --extra lmdb
   uv add --dev notebook ipykernel
   .venv/bin/python -m ipykernel install --user --name gestaltdb-py312 --display-name "GestaltDB Python 3.12"

Minimal Notebook Cell
---------------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import PickleSerializer

   graph_db = GraphDB(LevelDBStore("/tmp/leveldb_notebook_example"), PickleSerializer())

   graph_db.put_node(Node(node_id="drug-1"))
   graph_db.put_node(Node(node_id="protein-1"))
   graph_db.put_edge(Edge(
       edge_id="drug-1-protein-1",
       source="drug-1",
       target="protein-1",
       properties={"type": "drug-to-protein"},
   ))

   graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein")

Close Stores Between Runs
-------------------------

Repeated notebook execution can keep database files open. Close the graph before
rerunning setup cells that recreate stores.

.. code-block:: python

   graph_db.close()

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
