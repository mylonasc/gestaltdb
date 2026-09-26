"""GestaltDB graph storage, query, ingestion, and sampling toolkit.

GestaltDB stores attributed nodes, directed edges, native labels, typed
adjacency records, and secondary indexes on embedded key-value backends. The
main graph API lives in submodules rather than entirely at the package root:

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import PickleSerializer

   graph = GraphDB(LevelDBStore(path="graph_leveldb"), PickleSerializer())
   try:
       graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
       graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
       graph.put_edge(Edge(
           edge_id="alice-knows-bob",
           source="alice",
           target="bob",
           properties={"type": "knows"},
       ))
       result = graph.query(
           'MATCH (a:Person {name: "Alice"}) '
           'MATCH (a)-[:knows]->(b) '
           'RETURN a.id, b.id'
       )
   finally:
       graph.close()

Import core graph classes from ``gestaltdb.graphdb``, storage backends from
``gestaltdb.kvstores``, serializers from ``gestaltdb.serializers``, canonical
temporal values from ``gestaltdb.temporal``, and advanced sampling primitives
from ``gestaltdb.sampling``. Immutable temporal version models and write
descriptors live in ``gestaltdb.versioning``. This package root re-exports selected ingestion
enums/containers, temporal values, ``QueryResult``, and sampling helpers for
convenience.

Embedded backends and binary serializers are optional, independent extras. For
example, install ``gestaltdb[leveldb,msgpack]`` to combine LevelDB with
MessagePack or ``gestaltdb[lmdb,protobuf]`` to combine LMDB with Protobuf.
Pickle and JSON are built in; a base installation does not install a KV backend.

Important usage notes for agents and developers:

* Relationship traversal types come from ``Edge.properties["type"]``.
* ``GraphDB.query`` implements a broad Cypher read/write subset.
* Property indexes are explicit; create them before relying on property lookups.
* Explicit temporal version writes append history without changing current
  graph, Cypher, or sampler views.
* Use ``GraphDB.ingest_arrow`` and ``GraphDB.ingest_polars`` for tabular bulk
  ingestion, and use ``SamplerSnapshot``/``SamplerEngine`` for ML-oriented
  array-native sampling.

GestaltDB also ships offline interactive visualization. The D3.js + React
front end is prebuilt and packaged, so this needs no JavaScript toolchain:

.. code-block:: python

   from gestaltdb.viz.api import visualize_query

   figure = visualize_query(graph, 'MATCH (a:Person) RETURN a LIMIT 25')
   figure.save("/tmp/graph.html")  # open offline in any browser

See ``gestaltdb.viz.api`` (``VizOptions``, ``VizFigure``, ``visualize_*``),
``gestaltdb.viz.ir`` (deterministic payload builders), and
``GraphDB.visualize``.

GestaltDB also ships a packaged opencode skill and queryable user examples. In
an application project that uses GestaltDB, run ``python -m gestaltdb.agent_docs
install-opencode-skill`` to copy the packaged skill into
``.opencode/skills/gestaltdb-user-guide/SKILL.md``. Agents can also run
``python -m gestaltdb.agent_docs list`` or ``python -m gestaltdb.agent_docs get
cypher --examples`` to retrieve focused guidance from an installed package.
See repository ``AGENTS.md`` for contributor guidance, ``EXAMPLES.md`` for
runnable usage patterns, and ``docs/`` for the Sphinx user guide.
"""

from .sampling import AsyncBatchFeeder, HardNegativeConfig, SampledSubgraphBatch, SamplerEngine, SamplerSnapshot, SamplingHop, SamplingPattern
from .ingestion import ColumnarIngestionMode, EdgeList, IndexMaintenanceMode, NodeList
from .query_engine.cypher import QueryResult
from .temporal import TemporalContext, TemporalInstant, TemporalInterval

__all__ = [
    "ColumnarIngestionMode",
    "EdgeList",
    "IndexMaintenanceMode",
    "NodeList",
    "QueryResult",
    "AsyncBatchFeeder",
    "HardNegativeConfig",
    "SampledSubgraphBatch",
    "SamplerEngine",
    "SamplerSnapshot",
    "SamplingHop",
    "SamplingPattern",
    "TemporalContext",
    "TemporalInstant",
    "TemporalInterval",
]
