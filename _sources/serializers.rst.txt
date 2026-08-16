Serializers
===========

Serializers convert ``Node``, ``Edge``, and adjacency dictionaries to bytes for
storage. Choose the serializer based on portability and payload requirements.

Pickle Serializer
-----------------

``PickleSerializer`` is convenient for Python-only workflows.

.. code-block:: python

   from gestaltdb.serializers import PickleSerializer

   serializer = PickleSerializer()
   payload = {"id": "node-1", "properties": {"score": 1}}
   assert serializer.deserialize(serializer.serialize(payload)) == payload

JSON Serializer
---------------

``JSONSerializer`` is readable and portable for JSON-compatible values.

.. code-block:: python

   from gestaltdb.serializers import JSONSerializer

   serializer = JSONSerializer()
   data = serializer.serialize({"name": "Alice", "active": True})
   print(data.decode("utf-8"))

MessagePack Serializer
----------------------

``MessagePackSerializer`` is compact and supports bytes values.

.. code-block:: python

   from gestaltdb.serializers import MessagePackSerializer

   serializer = MessagePackSerializer()
   payload = {"edge_ids": [b"e1", b"e2"]}
   assert serializer.deserialize(serializer.serialize(payload)) == payload

Install the optional dependency first:

.. code-block:: sh

   python -m pip install ".[msgpack]"

Protobuf Serializer
-------------------

``ProtobufSerializer`` uses ``google.protobuf.Struct``. It tags bytes and ints so
they round-trip cleanly through Struct's JSON-like model.

.. code-block:: python

   from gestaltdb.serializers import ProtobufSerializer

   serializer = ProtobufSerializer()
   payload = {"count": 3, "raw": b"abc"}
   assert serializer.deserialize(serializer.serialize(payload)) == payload

Install the optional dependency first:

.. code-block:: sh

   python -m pip install ".[protobuf]"

Using a Serializer With GraphDB
-------------------------------

.. code-block:: python

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import MessagePackSerializer

   graph_db = GraphDB(LMDBStore(path="msgpack_lmdb"), MessagePackSerializer())
