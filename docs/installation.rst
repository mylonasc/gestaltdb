Installation
============

Python Version
--------------

GestaltDB targets Python 3.9 through 3.13. The LevelDB backend uses ``plyvel``;
at the time of writing, ``plyvel`` wheels are not available for Python 3.14 or
free-threaded Python builds. Use Python 3.12 or 3.13 for notebooks that need the
LevelDB backend.

Install With uv
---------------

From a local checkout:

.. code-block:: sh

   uv sync

For editable development:

.. code-block:: sh

   uv add --editable /path/to/gestaltdb

Install optional features only when you need them:

.. code-block:: sh

    uv add "/path/to/gestaltdb[lmdb,msgpack,protobuf]"
    uv add "/path/to/gestaltdb[leveldb]"
    uv add "/path/to/gestaltdb[fast-ingest]"

Install With pip
----------------

.. code-block:: sh

   python -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install .

Install optional backends and serializers:

.. code-block:: sh

   python -m pip install ".[lmdb]"
   python -m pip install ".[leveldb]"
   python -m pip install ".[rocksdb]"
   python -m pip install ".[fast-ingest]"
   python -m pip install ".[msgpack,protobuf]"
   python -m pip install ".[all]"

Install From GitHub
-------------------

.. code-block:: sh

   python -m pip install "gestaltdb[all] @ git+https://github.com/mylonasc/gestaltdb.git"

Notebook Kernel Example
-----------------------

Use a Python version supported by your selected backend and register it with
Jupyter:

.. code-block:: sh

   uv python install 3.12
   uv venv --python 3.12
   uv sync --extra leveldb --extra lmdb --extra msgpack --extra protobuf
   uv add --dev ipykernel
   .venv/bin/python -m ipykernel install --user --name gestaltdb-py312 --display-name "GestaltDB Python 3.12"

Optional Dependencies
---------------------

``lmdb``
   LMDB key-value backend.

``leveldb``
   LevelDB key-value backend through ``plyvel``.

``rocksdb``
   RocksDB key-value backend through ``pyrex-rocksdb>=0.3.0a0``.

``arrow``
   Arrow array support through ``pyarrow`` for columnar ingestion helpers.

``polars``
   Polars DataFrame support for columnar ingestion helpers.

``fast-ingest``
   Convenience extra for ``pyarrow``, ``polars``, and
   ``pyrex-rocksdb>=0.3.0a0``.

``msgpack``
   MessagePack serializer.

``protobuf``
   Protobuf Struct serializer for JSON-like dictionaries.

``bloom``
   Bloom-filter support through ``pybloom-live``.

``docs``
    Sphinx documentation build dependencies.

Pre-Release Installs
--------------------

Alpha releases use standard Python pre-release versions, for example
``0.2.0a0``. If a stable release with the same base version exists in the
future, install alpha builds explicitly:

.. code-block:: sh

   python -m pip install --pre "gestaltdb[fast-ingest]"
