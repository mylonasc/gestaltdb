Compatibility, Migration, and Recovery
======================================

Compatibility Contract
----------------------

GestaltDB 0.x has no 1.0 semantic-versioning guarantee, but persisted-data
changes are additive: current graph records remain readable, temporal history
uses a versioned and checksummed envelope, and sampler loaders either validate a
known format or fail closed. Documented public classes and methods are the
supported application surface. Underscore-prefixed methods, storage key layouts,
benchmark helpers, and serialized Python objects are implementation details.

The rule, truth-maintenance, modal, bitemporal Cypher, and temporal snapshot APIs
are public but evolving. Pin the GestaltDB minor version when deploying them and
exercise a restored copy of the database before upgrading. Arbitrary in-place
format conversion and opening one database with a different serializer are not
supported.

Backend and Serializer Matrix
-----------------------------

The temporal version, claim, rule, modal, and read-view APIs use the same
contract on every available backend and with Pickle, JSON, MessagePack, and
Protobuf serializers. Claims, rule definitions, commit descriptors, and modal
accessibility metadata use canonical serializer-neutral JSON; node and edge
payloads use the database serializer.

.. list-table::
   :header-rows: 1

   * - Backend
     - Temporal publication
     - Optional package
   * - LMDB
     - Native transaction across records, indexes, descriptor, and marker
     - ``lmdb``
   * - LevelDB
     - Marker-last publication; incomplete commits remain invisible
     - ``plyvel``
   * - PyRex/RocksDB default
     - Marker-last publication; one externally serialized temporal writer
     - ``pyrex-rocksdb``
   * - PyRex/RocksDB transactional
     - Native graph transaction; no native columnar writer
     - ``pyrex-rocksdb>=0.4.1``

.. list-table::
   :header-rows: 1

   * - Serializer
     - Temporal node/edge payloads
     - Legacy ``TimeIndexedEdge`` migration
   * - Pickle
     - Supported
     - Supported for historical datetime payloads
   * - JSON
     - Supported for JSON-compatible properties
     - Not supported for built-in datetime legacy payloads
   * - MessagePack
     - Supported when ``msgpack`` is installed
     - Not supported for built-in datetime legacy payloads
   * - Protobuf
     - Supported when ``protobuf`` is installed
     - Not supported for built-in datetime legacy payloads

Missing optional packages raise an actionable ``ImportError`` when the feature
is used. They are not required to import the base package. The test suite skips
only unavailable backend/serializer matrix entries.

Legacy Edge Migration
---------------------

``TimeIndexedEdge`` is a deprecated current-state key convention, not temporal
history. Back up the complete database, stop concurrent writers, open it with
the serializer that originally wrote it, and run:

.. code-block:: python

   migrated = graph.migrate_time_indexed_edges(
       delete_legacy=False,
       metadata={"operator": "release-2026-09"},
   )
   graph.close()

Reopen the database, validate representative ``get_edge_as_of`` and temporal
Cypher reads, then call ``migrate_time_indexed_edges(delete_legacy=True)`` to
remove legacy records. The migration groups records by logical edge ID. A record
is valid from its timestamp to the next timestamp for that ID; the final record
is open-ended. It appends one marker-backed temporal commit and does not
materialize mutable ``Edge`` records.

Retries skip exactly matching migration assertions. Cleanup begins only after a
visible temporal commit, so an interrupted cleanup can be rerun. Duplicate
timestamps, malformed payloads, mismatched timestamp keys, or legacy records
changed after a successful run fail before another commit is published. Do not
add new ``TimeIndexedEdge`` records after migration; use ``put_edge_version``.

Snapshots are immutable exports, not databases. Format-v1 snapshots remain
loadable without temporal or integrity guarantees. Rebuild from the source
database to obtain authenticated format-v2 snapshots; there is no in-place v1
upgrade. Unknown versions, incomplete publication, checksum mismatches, invalid
array shapes, or provenance mismatches fail closed.

Recovery and Retention
----------------------

For deferred writes, indexed reads fail until
``rebuild_temporal_indexes()`` or ``rebuild_deferred_indexes()`` succeeds.
Derived indexes may be rebuilt from canonical history. A marker-last crash can
leave reserved records, but an unmarked commit is invisible and the next commit
uses a new ID. Never delete internal metadata keys manually.

Hash, envelope, descriptor, marker, or canonical-record corruption raises
``TemporalCorruptionError``. Index rebuilds do not repair corrupted canonical
history: stop writes and restore the complete backend directory from a known
good backup. Copy databases only while closed or with a backend-supported
consistent backup mechanism. Snapshot corruption is recovered by rebuilding the
snapshot from a verified read view.

Temporal history, claims, rules, and commits currently have no pruning, TTL, or
compaction API. Retain all canonical history and include it in capacity plans.
Deleting mutable graph records does not delete temporal versions, and deleting
old snapshots does not affect source history.
