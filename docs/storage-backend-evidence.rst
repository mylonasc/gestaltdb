:orphan:

Historical Storage Backend Measurements
========================================

This page preserves the numeric observations previously embedded in
:doc:`storage-backends`. They are **historical local measurements**, not new
measurements of the documentation-restructuring branch or the v0.17.0 wheel.
The original prose did not bind every observation to an immutable source hash;
do not use these numbers as current release acceptance or a universal ranking.
For reproducible commands and stronger evidence qualifications, see
:doc:`performance`. Backend choice starts with :doc:`storage-backends`.

Object payloads and native ingestion
-------------------------------------

A local 100,000-node / 500,000-edge JSON-payload benchmark recorded RocksDB with
native columnar ingestion at **3.400 seconds total**, versus **3.948 seconds** for
LevelDB on the same Python JSON serialization path: **1.16x total** and **1.23x
write-phase** speedup. Avoiding Python JSON serialization with Polars yielded
larger gains in that run. This is workload-specific and does not imply every
serializer, graph shape or transaction mode benefits equally.

Deferred index maintenance
---------------------------

With 100,000 nodes / 500,000 edges, node property indexes on ``kind``/``group``
and an edge property index on ``weight``, a local observation recorded:

- inline write/index phase: **12.269 seconds**;
- deferred write phase: **1.407 seconds** (**8.72x** faster for that phase);
- subsequent rebuild: **12.973 seconds**;
- deferred ingest plus rebuild: **14.380 seconds**, **17.2% slower** overall.

Deferral changes *when* index work is paid; it does not inherently reduce total
work. Never report the write-phase ratio as the end-to-end speedup.

Plain versus transaction-capable PyRex
---------------------------------------

The original local comparison used 100,000 nodes, 500,000 edges, Pickle payloads,
chunks of 10,000, four RocksDB background threads, a 64 MiB write buffer and Bloom
filters. The transactional backend was opened with
``PyRexStore(transactional=True)`` and used the same public ingestion API, **not
one giant user transaction**.

=========================================== ========== ========== =================
RocksDB backend                             Node write Edge write Native columnar
=========================================== ========== ========== =================
default, Arrow payloads                     0.457 s    1.886 s    yes
transaction-capable, Arrow payloads         0.540 s    3.623 s    no
default, Polars payloads                    0.429 s    1.828 s    yes
transaction-capable, Polars payloads        0.545 s    3.670 s    no
=========================================== ========== ========== =================

The default native columnar writer and transaction-capable path have different
capabilities. Choose according to required atomicity and measured workload;
“nontransactional is always fastest” is not a supported guarantee.
