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
``gestaltdb.kvstores``, serializers from ``gestaltdb.serializers``, and advanced
sampling primitives from ``gestaltdb.sampling``. This package root re-exports
selected ingestion enums/containers, ``QueryResult``, and sampling helpers for
convenience.

Important usage notes for agents and developers:

* Relationship traversal types come from ``Edge.properties["type"]``.
* ``GraphDB.query`` implements a partial, read-only Cypher subset.
* Property indexes are explicit; create them before relying on property lookups.
* Use ``GraphDB.ingest_arrow`` and ``GraphDB.ingest_polars`` for tabular bulk
  ingestion, and use ``SamplerSnapshot``/``SamplerEngine`` for ML-oriented
  array-native sampling.

GestaltDB also ships a packaged opencode skill and queryable user examples. In
an application project that uses GestaltDB, run ``python -m gestaltdb.agent_docs
install-opencode-skill`` to copy the packaged skill into
``.opencode/skills/gestaltdb-user-guide/SKILL.md``. Agents can also run
``python -m gestaltdb.agent_docs list`` or ``python -m gestaltdb.agent_docs get
cypher --examples`` to retrieve focused guidance from an installed package.
See repository ``AGENTS.md`` for contributor guidance, ``EXAMPLES.md`` for
runnable usage patterns, and ``docs/`` for the Sphinx user guide.
"""

from .sampling import AsyncBatchFeeder, ExternalNeighborSamplingSpec, HardNegativeConfig, LayeredSampleBatch, NeighborSamplingSpec, RandomWalkBatch, SampledSubgraphBatch, SamplerEngine, SamplerSnapshot, SamplingHop, SamplingPattern
from .ingestion import ColumnarIngestionMode, EdgeList, IndexMaintenanceMode, NodeList
from .cypher import QueryResult

__all__ = [
    "ColumnarIngestionMode",
    "EdgeList",
    "IndexMaintenanceMode",
    "NodeList",
    "QueryResult",
    "AsyncBatchFeeder",
    "ExternalNeighborSamplingSpec",
    "HardNegativeConfig",
    "LayeredSampleBatch",
    "NeighborSamplingSpec",
    "RandomWalkBatch",
    "SampledSubgraphBatch",
    "SamplerEngine",
    "SamplerSnapshot",
    "SamplingHop",
    "SamplingPattern",
]
