Serializers
===========

A serializer encodes graph payloads as bytes; the selected KV backend persists
them. Choose them independently. For example, LMDB can store JSON or MessagePack,
and LevelDB can store JSON or Protobuf. This flexibility applies to **new compatible
stores**, not to reading one encoding with another decoder.

Choose a codec
---------------

.. list-table:: Built-in serializer choices
   :header-rows: 1

   * - Class / ``create`` registry name
     - Extra
     - Fit and limitations
   * - ``JSONSerializer`` / ``json``
     - None
     - Readable UTF-8, JSON-compatible properties plus GestaltDB temporal values.
       Raw ``bytes`` and arbitrary Python objects are not JSON values.
   * - ``PickleSerializer`` / ``pickle``
     - None
     - Python-only payloads. **Never unpickle untrusted data**; decoding can
       execute code. Python object portability is not a cross-language contract.
   * - ``MessagePackSerializer`` / ``messagepack``
     - ``gestaltdb[msgpack]``
     - Compact dictionaries/lists and binary values; not arbitrary Python objects.
   * - ``ProtobufSerializer`` / ``protobuf``
     - ``gestaltdb[protobuf]``
     - Uses ``google.protobuf.Struct`` with integer/bytes tagging; not an
       application-specific generated Protobuf schema.

The install extra ``msgpack`` and registry name ``messagepack`` are deliberately
different. Install backend and codec extras additively, for example
``python -m pip install "gestaltdb[lmdb,msgpack]"``. The bundle
``gestaltdb[serializers]`` installs both optional codecs, but no backend.

JSON and temporal properties
-----------------------------

**Standalone runnable example.** No optional dependency is needed. Temporal
scalars round-trip recursively through all four serializers; use the library's
temporal values rather than assuming every Python ``datetime`` object has the
same portable encoding.

.. code-block:: python

   from gestaltdb.serializers import JSONSerializer
   from gestaltdb.temporal import TemporalInstant

   codec = JSONSerializer()
   payload = {
       "name": "Aspirin",
       "observations": [{"recorded_at": TemporalInstant(0), "score": 0.9}],
   }
   encoded = codec.serialize(payload)
   assert isinstance(encoded, bytes)
   assert codec.deserialize(encoded) == payload

The encoded temporal representation uses library tags; it is not just an
untyped date string. Cypher temporal constructors and exact/range property indexes
have their own semantics (:doc:`cypher`, :doc:`property-filtering`). Scalar
property values do not automatically create temporal history.

Binary values with MessagePack
------------------------------

**Standalone runnable example.** Requires ``gestaltdb[msgpack]``.

.. code-block:: python

   from gestaltdb.serializers import MessagePackSerializer

   codec = MessagePackSerializer()
   payload = {"edge_ids": [b"e1", b"e2"]}
   assert codec.deserialize(codec.serialize(payload)) == payload

Protobuf integer and bytes preservation
----------------------------------------

**Standalone runnable example.** Requires ``gestaltdb[protobuf]``. Integer/bytes
tags preserve values that ordinary ``Struct`` numeric/string fields would not
represent in the same way.

.. code-block:: python

   from gestaltdb.serializers import ProtobufSerializer

   codec = ProtobufSerializer()
   payload = {"count": 2**60 + 1, "raw": b"abc"}
   assert codec.deserialize(codec.serialize(payload)) == payload

Pickle for trusted Python-only data
------------------------------------

**Standalone runnable example.** No optional dependency is needed. This payload
is created and decoded locally; it is not obtained from an untrusted supplier.

.. code-block:: python

   from gestaltdb.serializers import PickleSerializer

   codec = PickleSerializer()
   payload = {"ids": {"drug-1", "protein-1"}}
   assert codec.deserialize(codec.serialize(payload)) == payload

Using a serializer with GraphDB
-------------------------------

**Standalone runnable example.** Requires ``gestaltdb[lmdb,msgpack]``.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node

   with TemporaryDirectory() as root:
       path = f"{root}/graph"
       with GraphDB.create(path, backend="lmdb", serializer="messagepack") as graph:
           graph.put_node(Node("sample-1", properties={"raw": b"abc"}))
       with GraphDB.open(path) as reopened:
           assert reopened.get_node("sample-1").properties["raw"] == b"abc"

For direct construction import codec classes from ``gestaltdb.serializers`` and
pass an instance to ``GraphDB(store, codec)``. Import backend classes separately
from ``gestaltdb.kvstores``. The complete lifecycle example and the distinction
between direct construction and manifest-backed stores are in
:doc:`storage-backends`.

Compatibility and trust boundaries
-----------------------------------

- Reopen a store with its original compatible codec. Editing the manifest's
  serializer name or rebinding a decoder is not a data migration.
- Temporal values are supported across codecs, but arbitrary Python payload types
  are not universally interchangeable. Validate the values your application uses.
- Readable or tagged encoding is not source authentication, encryption, a
  resource sandbox, or a guarantee that supplier assertions are true.
- Codecs alone do not provide storage transactions, index maintenance or safe
  publication. These are graph/backend responsibilities.
- See :doc:`operations` for compatibility and recovery, and :doc:`performance`
  for why serialization costs should be measured separately from backend I/O.
