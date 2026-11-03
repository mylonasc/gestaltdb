Quickstart
==========

Create a graph, select a relationship with Cypher, and reuse a snapshot for
sampling. This standalone example uses disposable storage and closes the database
even if an operation fails. No database server is needed.

Install one backend
-------------------

Use a supported Python version (3.10–3.13) and install the LMDB extra for this
example:

.. code-block:: sh

   python -m pip install "gestaltdb[lmdb]"

The base package has no embedded KV backend. LMDB, LevelDB and PyRex/RocksDB are
swappable persistence choices; serializers are selected independently. This
example explicitly chooses LMDB and built-in JSON. See :doc:`storage-backends`
and :doc:`serializers` for other choices and their transaction/snapshot limits.

Create, query, and sample
-------------------------

**Standalone runnable example.** The query finds a drug's high-scoring protein
target. A property index supports direct name lookup; native labels and
relationship types are indexed automatically.

.. code-block:: python

   from tempfile import TemporaryDirectory

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.sampling import SamplerEngine

   with TemporaryDirectory() as root:
       with GraphDB.create(
           f"{root}/graph", backend="lmdb", serializer="json"
       ) as graph_db:
           graph_db.put_nodes([
               Node("drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
               Node("protein-1", labels=["Protein"], properties={"name": "PTGS1"}),
           ])
           graph_db.put_edge(Edge(
               "binding-1", "drug-1", "protein-1",
               properties={"type": "binds", "score": 0.9},
           ))

           graph_db.create_node_property_index("name")
           matches = graph_db.nodes_by_property("name", "Aspirin")
           assert [node.get_id for node in matches] == ["drug-1"]

           result = graph_db.query(
               "MATCH (d:Drug {name: $name})-[r:binds]->(p:Protein) "
               "WHERE r.score >= $minimum "
               "RETURN d.id AS drug, p.name AS protein, r.score AS score",
               parameters={"name": "Aspirin", "minimum": 0.8},
           )
           assert result.records == [
               {"drug": "drug-1", "protein": "PTGS1", "score": 0.9}
           ]
           print(result.records)

           # Build once while the source is open; reuse after it closes.
           snapshot = graph_db.build_sampler_snapshot(f"{root}/sampler")

       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       sample = engine.sample_neighbors_external(
           ["drug-1"], fanout=5, relations=["binds"],
       )
       assert sample.to_external(engine.snapshot)["neighbor_nodes"].tolist() == [
           "protein-1"
       ]

       # create() wrote a manifest: open() recovers the backend and codec.
       with GraphDB.open(f"{root}/graph") as reopened:
           assert reopened.get_node("drug-1").properties["name"] == "Aspirin"

The printed result is::

   [{'drug': 'drug-1', 'protein': 'PTGS1', 'score': 0.9}]

What the example establishes
----------------------------

- **Graph model:** nodes have stable IDs, labels and properties. Edges have their
  own IDs, directed endpoints and properties; ``properties["type"]`` identifies
  the relationship type. Multiple distinct edges may connect the same nodes.
  Mathematically, this is a directed multigraph :math:`G=(V,E,s,t)`, where
  :math:`s,t:E\to V` map each edge to its source and target; labels/properties
  attach to those entities.
- **Access choices:** use direct reads when you know IDs, explicit indexes for
  property cohorts, and Cypher for graph patterns and composed results. Cypher
  supports writes too, but is a documented subset, not every Cypher/GQL feature
  (:doc:`cypher`).
- **Index requirements:** label/type indexes are automatic; property indexes are
  explicit. Deferred ingestion requires index rebuilds before index-backed reads
  (:doc:`property-filtering`).
- **Persistence:** ``create`` writes a self-describing database directory;
  ``open`` reuses it. Context managers close handles. The temporary directory here
  intentionally deletes the demonstration data when the block ends.
- **Sampling:** the snapshot is immutable and does not include later graph writes.
  Array-native sampling needs no open database. ``_external`` methods accept
  application IDs; ``to_external`` converts compact output IDs back for inspection.
  Memmap avoids eager array loading, not all memory use (:doc:`typed-sampling`).

Filtering the query's rows or seeds does **not** filter every sampled hop. Build a
filtered snapshot with ``node_filter`` / ``edge_filter`` when that is the intended
graph; see :doc:`property-filtering`. This example uses the whole two-node graph.

Choose your next path
---------------------

.. list-table:: From application goal to focused guide
   :header-rows: 1
   :widths: 45 55

   * - I want to…
     - Continue with…
   * - Choose persistence, codecs or current-snapshot guarantees
     - :doc:`property-graph`, :doc:`storage-backends`, :doc:`serializers`, :doc:`operations`
   * - Load Arrow/Polars tables or select indexed cohorts
     - :doc:`data-access`, :doc:`ingestion`, :doc:`property-filtering`
   * - Query paths, aggregate, or mutate with Cypher
     - :doc:`cypher-tutorial`, :doc:`cypher`
   * - Draw layers, subgraphs, walks or temporal negatives
     - :doc:`typed-sampling`, :doc:`random-walks`
   * - Inspect historical state at valid/system time
     - :doc:`temporal-versioning`
   * - Record sourced claims, derive stored rules or inspect belief
     - :doc:`temporal-versioning`, :doc:`modal-epistemic`, :doc:`inference-audit`
   * - Classify entities from ontology axioms
     - :doc:`ontology`
   * - Evaluate standalone binary/typed Datalog
     - :doc:`api-workflows`, :doc:`reasoning-typed`, :doc:`reasoning-negation`
   * - Compute intervals over which conclusions hold
     - :doc:`reasoning-temporal`
   * - Connect a graph to reasoning and source-verified saved state
     - :doc:`ontology-graph`, :doc:`reasoning-authenticated-sessions`
   * - Visualize or exchange results
     - :doc:`visualization`, :doc:`networkx`, :doc:`notebooks`

Ordinary mutable writes do not create temporal history automatically. Ontology
compilation and standalone reasoning do not silently change graph records,
Cypher results or sampler membership. The :doc:`architecture` guide explains
these separate responsibilities and their explicit adapters.
