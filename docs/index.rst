GestaltDB Documentation
=======================

GestaltDB is an embedded property-graph toolkit with swappable **LMDB, LevelDB and
RocksDB/PyRex backends**, independent serializers, indexed filtering, and Cypher
reads and writes. **Arrow/Polars ingestion** and reusable **array-native sampling**
support graph-data and ML pipelines. Temporal history, sourced claims, ontology
compilation and standalone Datalog add explicit knowledge-representation workflows.

Start with :doc:`quickstart` to create a disposable graph, query it, and sample
neighbors. You can use the database, sampling or standalone reasoning without
adopting the whole stack. See :doc:`architecture` for how the components connect.

Choose a capability
--------------------

.. list-table:: Find the path for your application
   :header-rows: 1
   :widths: 45 55

   * - Goal
     - Start here
   * - Store and query a property graph
     - :doc:`quickstart`, :doc:`cypher`
   * - Choose a backend, codec or persistence mode
     - :doc:`storage-backends`, :doc:`serializers`
   * - Load tables and select indexed cohorts
     - :doc:`api-workflows`, :doc:`property-filtering`
   * - Prepare graph-learning batches
     - :doc:`typed-sampling`, :doc:`random-walks`
   * - Query what applied then or what was recorded then
     - :doc:`temporal-versioning`
   * - Represent sourced/conflicting claims and reason over worlds
     - :doc:`temporal-versioning`, :doc:`modal-epistemic`
   * - Classify entities using semantic axioms
     - :doc:`ontology`
   * - Apply Datalog rules, literals, negation or aggregates
     - :doc:`api-workflows`, :doc:`reasoning-typed`, :doc:`reasoning-negation`
   * - Compute when conclusions hold
     - :doc:`reasoning-temporal`
   * - Integrate reasoning with a database or saved state
     - :doc:`ontology-graph`, :doc:`reasoning-authenticated-sessions`
   * - Explore or exchange results
     - :doc:`visualization`, :doc:`networkx`
   * - Operate, recover or measure workloads
     - :doc:`operations`, :doc:`performance`

Important choices
------------------

- **Live traversal or reusable arrays?** Live sampling reads typed adjacency;
  ``SamplerSnapshot`` / ``SamplerEngine`` reuse compact arrays for repeated draws.
  Filter the eligible graph at build time, not just the seeds. Snapshot construction,
  loading and steady-state sampling have separate costs (:doc:`typed-sampling`).
- **Historical data or temporal reasoning?** Version history selects what is
  visible at valid/system time; interval reasoning computes when conclusions
  follow. Recording history does not run Datalog (:doc:`reasoning-temporal`).
- **Stored rules or standalone Datalog?** ``graph.rules`` operates on temporal
  claims. Standalone engines consume explicit programs/facts; database extraction,
  admission and publication use explicit adapters (:doc:`api-workflows`).
- **Evaluation or publication?** A computed result does not automatically modify
  graph records. Source authentication checks source/state identity, not the truth
  of assertions or user credentials (:doc:`reasoning-authenticated-sessions`).

Guide navigation
-----------------

The first groups cover application workflows. Advanced integration pages retain
their exact capability and authentication contracts; engineering references and
benchmarks are separated from the first-run path.

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   installation
   quickstart
   architecture

.. toctree::
   :maxdepth: 1
   :caption: Property Graph, Persistence, and Queries

   storage-backends
   serializers
   property-filtering
   cypher

.. toctree::
   :maxdepth: 1
   :caption: Sampling and Graph ML

   typed-sampling
   random-walks
   sampling-consistency

.. toctree::
   :maxdepth: 1
   :caption: Temporal and Epistemic Knowledge

   temporal-versioning
   modal-epistemic
   inference-audit

.. toctree::
   :maxdepth: 1
   :caption: Ontology and Datalog

   ontology
   reasoning-typed
   reasoning-negation
   reasoning-proofs
   reasoning-demand
   reasoning-validation
   reasoning-equality

.. toctree::
   :maxdepth: 1
   :caption: Temporal Reasoning

   reasoning-intervals
   reasoning-temporal
   reasoning-ranges
   reasoning-conformance

.. toctree::
   :maxdepth: 1
   :caption: Advanced Reasoning Integration

   ontology-graph
   reasoning-current-frontier
   reasoning-current-admission
   reasoning-queries
   reasoning-generation-identity
   reasoning-sessions
   reasoning-authenticated-sessions
   reasoning-typed-execution
   reasoning-range-admission
   reasoning-bound-range-publication
   ontology-publication
   reasoning-authenticated-inferred
   reasoning-executors
   reasoning-extraction
   reasoning-native-claims
   reasoning-native-locator-claims

.. toctree::
   :maxdepth: 1
   :caption: Visualization and Interoperability

   visualization
   networkx
   notebooks

.. toctree::
   :maxdepth: 1
   :caption: Operations and Performance

   operations
   performance
   ontology-benchmarks

.. toctree::
   :maxdepth: 1
   :caption: API and Engineering Reference

   api
   api-workflows
   reasoning-profiles
   reasoning-testing
   code-quality
