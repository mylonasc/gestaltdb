API Reference
=============

Filtering and Sampling Entry Points
----------------------------------------

- **Property-graph filtering:** ``GraphDB`` label/type, exact/range property,
  ID-iterator, and count helpers. See :doc:`property-filtering` for index setup
  and cohort-to-snapshot examples, and :doc:`cypher` for query predicates.
- **Live sampling:** ``GraphDB.sample_neighbors``, ``sample_typed_paths``, and
  ``sample_typed_subgraph`` with ``SamplingHop`` / ``SamplingPattern``.
- **Repeated array-native sampling:** ``SamplerSnapshot`` / ``SamplerEngine``
  for seed, weighted-neighbor, layered, walk, multihop, and negative sampling in
  RAM or memmap mode. See :doc:`typed-sampling` for the API map and ID rules,
  and :doc:`performance` for cost boundaries and benchmarks.

Graph Models and Database
-------------------------

.. automodule:: gestaltdb.graphdb
   :members:
   :undoc-members:
   :show-inheritance:

Temporal Semantics
------------------

.. automodule:: gestaltdb.temporal
   :members:
   :show-inheritance:

Temporal Version History
------------------------

.. automodule:: gestaltdb.versioning
   :members:
   :show-inheritance:

Epistemic Claims
----------------

.. automodule:: gestaltdb.epistemic
   :members:
   :show-inheritance:

Positive Horn Rules
-------------------

.. automodule:: gestaltdb.rules
   :members:
   :show-inheritance:

Modal Evaluation
----------------

.. automodule:: gestaltdb.modal
   :members:
   :show-inheritance:

Consistent Read Views
---------------------

.. automodule:: gestaltdb.readview
   :members:
   :show-inheritance:

Type Hierarchies
----------------

.. automodule:: gestaltdb.hierarchy
   :members:
   :show-inheritance:

Sampling Configuration
----------------------

.. automodule:: gestaltdb.sampling
   :members: SamplingHop, SamplingPattern, as_sampling_hop, as_sampling_pattern
   :show-inheritance:

Sampling Engine
---------------

.. automodule:: gestaltdb.sampling.engine
   :members:
   :show-inheritance:

Sampling Snapshots
------------------

.. automodule:: gestaltdb.sampling.snapshot
   :members:
   :show-inheritance:

Sampled Batches
---------------

.. automodule:: gestaltdb.sampling.batch
   :members:
   :show-inheritance:

Sampling Negatives
------------------

.. automodule:: gestaltdb.sampling.negatives
   :members:
   :show-inheritance:

Sampling Feeders
----------------

.. automodule:: gestaltdb.sampling.feeder
   :members:
   :show-inheritance:

Columnar Ingestion
------------------

.. automodule:: gestaltdb.ingestion
   :members:
   :undoc-members:
   :show-inheritance:

Cypher Queries
--------------

.. automodule:: gestaltdb.query_engine.cypher
   :members:
   :undoc-members:
   :show-inheritance:

Key-Value Stores
----------------

.. automodule:: gestaltdb.kvstores
   :members:
   :undoc-members:
   :show-inheritance:

Serializers
-----------

.. automodule:: gestaltdb.serializers
   :members:
   :undoc-members:
   :show-inheritance:

Visualization IR
----------------

.. automodule:: gestaltdb.viz.ir
   :members:
   :undoc-members:
   :show-inheritance:

Visualization Artifacts and API
-------------------------------

.. automodule:: gestaltdb.viz.api
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: gestaltdb.viz.html
   :members:
   :undoc-members:
   :show-inheritance:
