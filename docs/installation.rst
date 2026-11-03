Installation
============

Python Version
--------------

Use **Python 3.10 through 3.13** (``>=3.10,<3.14``). Python 3.14 and free-threaded
builds are not declared supported. Optional backends have their own platform and
wheel requirements; ``plyvel`` may need a native LevelDB development package
when built from source. In a notebook, install into the interpreter used by its
kernel, not an unrelated shell environment (:doc:`notebooks`).

Choose only the features you use
---------------------------------

.. list-table:: Application installation choices
   :header-rows: 1

   * - Task
     - Requirement
     - Start here
   * - Array sampling, standalone reasoning or offline visualization
     - ``gestaltdb`` (base; includes NumPy and Lark, no KV backend)
     - :doc:`sampling-introduction`, :doc:`reasoning-introduction`, :doc:`visualization`
   * - Store/query a graph with JSON or Pickle
     - ``gestaltdb[lmdb]``, ``gestaltdb[leveldb]`` or ``gestaltdb[rocksdb]``
     - :doc:`storage-backends`
   * - Add a binary payload codec
     - Add ``msgpack`` or ``protobuf`` to the chosen backend extra
     - :doc:`serializers`
   * - Load Arrow or Polars tables
     - Add ``arrow`` or ``polars`` plus a backend; ``fast-ingest`` includes PyRex
     - :doc:`ingestion`
   * - Exchange NetworkX or static GEXF
     - Add ``networkx`` and a backend for database import/export
     - :doc:`networkx`
   * - Import the supported offline RDF ontology fragment
     - ``gestaltdb[rdf]``; no database required for standalone compilation
     - :doc:`ontology`

Extras are additive: ``gestaltdb[lmdb,msgpack]`` selects storage and codec
independently. ``backends`` and ``serializers`` are convenience bundles, not
required for every application. ``all`` additionally installs external benchmark
clients; it is not the default runtime recommendation and does not include the
separate ``rdf`` extra. Visualization rendering
requires neither the ``docs`` extra nor Node/npm; Jupyter itself is installed
separately if you want notebook display.

Install With uv
---------------

In your application project:

.. code-block:: sh

   uv add "gestaltdb[lmdb]"

For editable use of a local checkout:

.. code-block:: sh

   uv add --editable /path/to/gestaltdb

Install optional features only when you need them:

.. code-block:: sh

    uv add "/path/to/gestaltdb[leveldb,msgpack]"
    uv add "/path/to/gestaltdb[lmdb,protobuf]"
    uv add "/path/to/gestaltdb[backends,serializers]"
    uv add "/path/to/gestaltdb[fast-ingest]"

Install With pip
----------------

.. code-block:: sh

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install "gestaltdb[lmdb]"

The base package includes the Pickle and JSON serializers but no embedded KV
backend. Install backend and serializer extras independently; extras are
additive, so no backend/serializer combination extras are needed.

Array-native sampling from ``SamplerSnapshot.from_edge_arrays`` needs only the
base package (including NumPy), not a KV backend or a separate sampling extra.
For the database-backed filtering/sampling examples, install ``gestaltdb[lmdb]``.
See :doc:`typed-sampling` for a backend-free quickstart and
:doc:`property-filtering` for building a snapshot from an embedded database.

For a checkout, combine optional extras as follows:

.. code-block:: sh

   python -m pip install ".[leveldb,msgpack]"
   python -m pip install ".[lmdb,protobuf]"
   python -m pip install ".[rocksdb]"
   python -m pip install ".[backends,serializers]"
   python -m pip install ".[fast-ingest]"
   python -m pip install ".[msgpack,protobuf]"
   python -m pip install ".[all]"

``GraphDB.create`` retains ``backend="pyrex"`` as its compatibility default,
so parameterless creation requires ``gestaltdb[rocksdb]``. GestaltDB never
silently substitutes another backend; pass an installed backend explicitly.

Install From GitHub
-------------------

.. code-block:: sh

   python -m pip install "gestaltdb[lmdb] @ git+https://github.com/mylonasc/gestaltdb.git"

This installs source, not the published wheel. Pin a reviewed commit when you
need reproducible source installs; choose the extras required by your application.

Notebook Kernel Example
-----------------------

Use a Python version supported by your selected backend and register it with
Jupyter:

.. code-block:: sh

   python -m pip install "gestaltdb[lmdb]" ipykernel
   python -m ipykernel install --user --name gestaltdb --display-name "GestaltDB"

Optional Dependencies
---------------------

``lmdb``
   LMDB key-value backend.

``leveldb``
   LevelDB key-value backend through ``plyvel``.

``rocksdb``
   RocksDB key-value backend through ``pyrex-rocksdb>=0.7.1``.

``backends``
   Convenience bundle containing the LMDB, LevelDB, and RocksDB backends.

``arrow``
   Arrow array support through ``pyarrow`` for columnar ingestion helpers.

``polars``
   Polars DataFrame support for columnar ingestion helpers.

``fast-ingest``
   Convenience extra for ``pyarrow``, ``polars``, and
   ``pyrex-rocksdb>=0.7.1``.

``msgpack``
   MessagePack serializer.

``protobuf``
   Protobuf Struct serializer for JSON-like dictionaries.

``serializers``
   Convenience bundle containing MessagePack and Protobuf. Pickle and JSON need
   no extra.

``bloom``
   Bloom-filter support through ``pybloom-live``.

``networkx``
   Materialized graph exchange and static GEXF loading; includes NetworkX and
   ``defusedxml``. Not automatic NetworkX algorithm dispatch over live storage.

``rdf``
   Offline import of the selected RDF ontology fragment through RDFLib.

``docs``
    Sphinx documentation build dependencies.

Pre-Release Installs
--------------------

Pre-releases use standard Python version identifiers. Pip normally selects
stable releases; use ``--pre`` to allow available pre-release builds explicitly:

.. code-block:: sh

   python -m pip install --pre "gestaltdb[fast-ingest]"
