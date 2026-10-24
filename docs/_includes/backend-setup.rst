Choose one backend
~~~~~~~~~~~~~~~~~~

Install the extra shown in your tab, then copy its Python setup. All subsequent
examples on this page use the same ``graph_db`` handle. These are disposable
example directories; use a new path for each run. Close the handle when finished.

.. tab-set::

   .. tab-item:: LMDB
      :sync: lmdb

      .. code-block:: console

         python -m pip install "gestaltdb[lmdb]"

      .. code-block:: python

         from gestaltdb.graphdb import Edge, GraphDB, Node

         graph_db = GraphDB.create("example_lmdb", backend="lmdb", serializer="json")

   .. tab-item:: LevelDB
      :sync: leveldb

      .. code-block:: console

         python -m pip install "gestaltdb[leveldb]"

      .. code-block:: python

         from gestaltdb.graphdb import Edge, GraphDB, Node

         graph_db = GraphDB.create("example_leveldb", backend="leveldb", serializer="json")

   .. tab-item:: RocksDB / PyRex
      :sync: pyrex

      .. code-block:: console

         python -m pip install "gestaltdb[rocksdb]"

      .. code-block:: python

         from gestaltdb.graphdb import Edge, GraphDB, Node

         graph_db = GraphDB.create("example_rocksdb", backend="pyrex", serializer="json")

The RocksDB install extra is ``rocksdb``; its backend registry name is ``pyrex``.
Only the selected backend needs to be installed. See :doc:`/storage-backends`
for transaction, snapshot, and native-ingestion differences.
