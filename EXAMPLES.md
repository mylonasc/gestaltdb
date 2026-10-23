# GestaltDB Examples

These examples are designed for developers and agents who need working patterns quickly. They use explicit imports from submodules because the package root intentionally exports only selected helpers.

**Filtering and sampling workflows:** [Indexed property lookups](#index-and-lookup-properties) · [Live typed sampling](#traverse-and-sample-typed-relationships) · [Filtered, reusable sampler snapshots](#build-and-use-a-sampler-snapshot). The [sampling guide](https://mylonasc.github.io/gestaltdb/typed-sampling.html) covers all policies; the [filtering guide](https://mylonasc.github.io/gestaltdb/property-filtering.html) explains seed selection versus filtering every sampled hop.

## Reason Over Named Entities Without a Database

```python
from gestaltdb.ontology import Domain, Ontology, TYPE_PREDICATE, compile_ontology
from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits

plan = compile_ontology(Ontology("people", [Domain("worksFor", "Person")]))
result = DatalogEngine().evaluate(
    plan.program, [Fact("alice", "worksFor", "acme")],
    limits=ReasoningLimits(), collect_proofs=True,
)
assert Fact("alice", TYPE_PREDICATE, "Person") in result
```

This selected positive fragment is not full OWL. Inference is read-only and proof
truncation is distinct from inference failure. See `docs/ontology.rst` for the
explicit pinned-claims adapter and `docs/ontology-benchmarks.rst` for cost bounds.

## Create, Query, and Close a Graph

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge(
            edge_id="alice-knows-bob",
            source="alice",
            target="bob",
            properties={"type": "knows", "since": 2024},
        ))

        result = graph.query(
            'MATCH (a:Person {name: "Alice"}) '
            'MATCH (a)-[:knows]->(b) '
            'RETURN a.id AS source, b.name AS target'
        )
        print(result.columns)
        print(result.records)
    finally:
        graph.close()
```

## Record Contradictory Epistemic Claims

Claims keep polarity separate from confidence and preserve contradictory
sources. Status is open-world and evaluated at a valid-time point and a
system-time commit horizon.

```python
from tempfile import TemporaryDirectory

from gestaltdb import ClaimStatus
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        positive = graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="positive", agent="source:hr-feed", source="hr.csv",
            confidence=0.97, world="reported", provenance={"row": 42},
            valid_from="2024-01-01T00:00:00Z",
        )
        graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="negative", agent="source:investigator", confidence=0.6,
            world="reported", valid_from="2024-01-01T00:00:00Z",
        )
        assert graph.claim_status(
            "alice", "WORKS_FOR", "acme",
            valid_time="2024-06-01T00:00:00Z", world="reported",
        ) is ClaimStatus.BOTH

        reviewed = graph.correct_claim(
            positive.logical_id,
            supersedes_version_id=positive.version_id,
            confidence=0.99,
            provenance={"row": 42, "reviewed": True},
        )
        graph.retract_claim(
            positive.logical_id,
            supersedes_version_id=reviewed.version_id,
            valid_from="2025-01-01T00:00:00Z",
            reason="feed correction",
        )
    finally:
        graph.close()
```

## Derive Temporal Claims With Rules

Rules use positive entity claims from one world. Each support contributes the
intersection of its premise validity, and provenance records the exact rule and
premise versions used.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(f"{tmpdir}/graph", backend="leveldb", serializer="json")
    try:
        employment = graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="positive", agent="source:hr", world="reported",
            valid_from="2024-01-01T00:00:00Z",
        )
        graph.assert_claim(
            subject="acme", predicate="MEMBER_OF", object="industry",
            polarity="positive", agent="source:registry", world="reported",
            valid_from="2024-03-01T00:00:00Z",
        )
        graph.create_rule(
            "employment-implies-affiliation",
            when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
            then=("?p", "AFFILIATED_WITH", "?g"),
        )

        result = graph.run_rules(
            as_of="2024-06-01T00:00:00Z",
            max_iterations=100,
            max_derivations=10_000,
        )
        assert result.derived_count == 1
        derived = result.versions[0]
        assert derived.claim.subject == "alice"
        assert derived.claim.object == "industry"
        assert len(derived.claim.provenance["justifications"]) == 1

        explanation = graph.explain_claim(
            derived.logical_id,
            valid_time="2024-06-01T00:00:00Z",
        )
        assert explanation.root_version_id == derived.version_id

        graph.retract_claim(
            employment.logical_id,
            supersedes_version_id=employment.version_id,
            reason="source correction",
        )
        maintained = graph.maintain_truth()
        assert maintained.retracted_count == 1
        assert graph.get_claim_as_of(
            derived.logical_id,
            valid_time="2024-06-01T00:00:00Z",
        ) is None
    finally:
        graph.close()
```

## Flag Absent Certifications With Stratified Negation

Negated atoms are closed-world absence checks, not negative claims. List the
complete predicates in `closed_world`; unstratifiable programs fail before the
rule catalog changes. Absence evidence appears in explanations as `absence`
nodes.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(f"{tmpdir}/graph", backend="leveldb", serializer="json")
    try:
        graph.assert_claim(
            subject="supplier-1", predicate="SHIPS", object="widgets",
            polarity="positive", agent="erp", valid=(0, 100),
        )
        graph.create_rule(
            "review-uncertified",
            when=[("?s", "SHIPS", "?goods")],
            then=("?s", "REQUIRES_SAFETY_REVIEW", "yes"),
            not_when=[("?s", "CERTIFIED", "iso9001")],
            closed_world=["CERTIFIED"],
        )
        graph.run_rules(as_of=10)
        flagged = next(graph.iter_claims_as_of(
            valid_time=10, predicate="REQUIRES_SAFETY_REVIEW"))
        assert flagged.claim.subject == "supplier-1"

        explanation = graph.explain_claim(flagged.logical_id, valid_time=10)
        assert any(node.kind == "absence" for node in explanation.nodes)

        graph.assert_claim(
            subject="supplier-1", predicate="CERTIFIED", object="iso9001",
            polarity="positive", agent="audit", valid=(0, 100),
        )
        assert graph.maintain_truth(as_of=10).retracted_count == 1
        assert list(graph.iter_claims_as_of(
            valid_time=10, predicate="REQUIRES_SAFETY_REVIEW")) == []
    finally:
        graph.close()
```

## Evaluate Bounded Belief and Knowledge

Accessibility is an explicit temporal fact. Belief and knowledge use separate
agent frames, and every evaluation has finite depth and state limits.

```python
from tempfile import TemporaryDirectory

from gestaltdb import ClaimStatus
from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(f"{tmpdir}/graph", backend="leveldb", serializer="json")
    try:
        graph.assert_world_accessibility(
            agent="alice", from_world="actual", to_world="alice-belief",
            kind="belief", valid_from="2025-01-01T00:00:00Z",
        )
        graph.assert_claim(
            subject="bob", predicate="LOCATED_IN", object="paris",
            polarity="positive", agent="source:registry", world="alice-belief",
            confidence=0.9, valid_from="2025-01-01T00:00:00Z",
        )
        result = graph.entails(
            "alice",
            {"subject": "bob", "predicate": "LOCATED_IN", "object": "paris"},
            "BELIEVES",
            world="actual",
            valid_time="2025-06-01T00:00:00Z",
            max_depth=4,
            max_states=1000,
        )
        assert result.status is ClaimStatus.SUPPORTED

        cypher = graph.query(
            "CALL kg.entails($agent, $claim, 'BELIEVES', $options) "
            "YIELD status, confidence, explanation "
            "RETURN status, confidence, explanation",
            parameters={
                "agent": "alice",
                "claim": {"subject": "bob", "predicate": "LOCATED_IN", "object": "paris"},
                "options": {"world": "actual", "validTime": "2025-06-01T00:00:00Z"},
            },
        )
        assert cypher.records[0]["status"] == "supported"
    finally:
        graph.close()
```

## Use a Self-Describing Store

`GraphDB.create` writes a manifest next to the database. `GraphDB.open` uses that manifest to choose the backend and serializer.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(
        f"{tmpdir}/graph",
        backend="leveldb",
        serializer="json",
        indexed_node_properties=["kind"],
    )
    try:
        graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}))
        graph.put_node(Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}))
        graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "binds"}))
    finally:
        graph.close()

    reopened = GraphDB.open(f"{tmpdir}/graph")
    try:
        print(reopened.get_node(b"drug-1").properties["kind"])
        print(reopened.manifest["serializer"]["name"])
    finally:
        reopened.close()
```

## Choose a Backend and Serializer

Backend and serializer extras are independent and additive. Install exactly the
pair you intend to use; no combination-specific extra is needed:

```sh
python -m pip install "gestaltdb[leveldb,msgpack]"
python -m pip install "gestaltdb[lmdb,protobuf]"
```

```python
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, MessagePackSerializer, PickleSerializer

graph_lmdb = GraphDB(LMDBStore(path="graph_lmdb", map_size=2**30), PickleSerializer())
graph_leveldb = GraphDB(LevelDBStore(path="graph_leveldb"), MessagePackSerializer())
graph_rocksdb = GraphDB(PyRexStore(path="graph_rocksdb"), JSONSerializer())

graph_lmdb.close()
graph_leveldb.close()
graph_rocksdb.close()
```

Use `LevelDBStore` for small local examples, `LMDBStore` when a fixed LMDB map is appropriate, and `PyRexStore` for RocksDB-backed bulk ingestion.

## Index and Lookup Properties

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin", "score": 0.9}),
            Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen", "score": 0.7}),
        ])
        graph.put_edges_bulk([
            Edge(edge_id="e1", source="drug-1", target="drug-2", properties={"type": "similar", "score": 0.8}),
        ])

        graph.create_node_property_index("name")
        graph.create_node_property_index("score")
        graph.create_edge_property_index("score")

        print([node.get_id for node in graph.nodes_by_label("Drug")])
        print([node.get_id for node in graph.nodes_by_property("name", "Aspirin")])
        print([node.get_id for node in graph.nodes_by_property_range("score", 0.8, None)])
        print([edge.get_id for edge in graph.edges_by_type_property_range("similar", "score", 0.75, None)])
    finally:
        graph.close()
```

## Read and Write with Cypher

```python
result = graph.query(
    'MATCH (d:Drug) '
    'WHERE d.score >= $minimum '
    'RETURN d.id AS id, d.name AS name '
    'ORDER BY name',
    parameters={"minimum": 0.8},
)

for record in result:
    print(record["id"], record["name"])
```

Cypher covers indexed scans, typed traversal, read composition, paths,
aggregation, and writes. Writes use backend transactions when available:

```python
result = graph.query(
    'MATCH (p:Person) '
    'WITH p ORDER BY p.age DESC LIMIT 10 '
    'MATCH (p)-[:member_of]->(t:Team) '
    'RETURN p.name AS name, t.name AS team'
)
```

```python
totals = graph.query(
    'MATCH (p:Person) '
    'RETURN p.department AS department, count(*) AS total '
    'ORDER BY total DESC'
)
```

```python
bands = graph.query(
    'MATCH (p:Person) '
    'RETURN p.name AS name, '
    'CASE WHEN p.age >= 35 THEN "senior" ELSE "junior" END AS band, '
    '[t IN p.tags WHERE t STARTS WITH "a" | t] AS tags'
)
```

```python
created = graph.query(
    'MERGE (a:Person {id: "alice"}) ON CREATE SET a.name = "Alice" '
    'MERGE (b:Person {id: "bob"}) ON CREATE SET b.name = "Bob" '
    'MERGE (a)-[r:KNOWS {id: "alice-knows-bob"}]->(b) '
    'ON CREATE SET r.since = 2024 '
    'RETURN a.name AS source, b.name AS target, r.since AS since'
)
assert created.records == [{"source": "Alice", "target": "Bob", "since": 2024}]

paths = graph.query(
    'MATCH p = (a:Person {id: "alice"})-[:KNOWS*1..2]->(friend) '
    'OPTIONAL MATCH (friend)-[:KNOWS]->(other) '
    'RETURN friend.name AS friend, length(p) AS hops, other.name AS other '
    'ORDER BY friend'
)
```

Inspect schema metadata and call the registered sampling procedure:

```python
graph.create_node_property_index("name")
graph.create_edge_property_index("since")
assert graph.query("SHOW INDEXES").columns == ("entityType", "properties")

sampled = graph.query(
    'CALL pg.sample_typed_paths($seeds, $pattern) '
    'YIELD path AS sampled RETURN sampled LIMIT 1',
    parameters={
        "seeds": ["alice"],
        "pattern": [{"edge_type": "KNOWS", "direction": "out", "sample_size": 2}],
    },
)
```

Node label matching is exact by default. Pass a `LabelHierarchy` to expand a
parent label to its transitive sub-types for one query:

```python
from gestaltdb import LabelHierarchy

transport = LabelHierarchy({
    "Ocean_Freight": ["Transport_Edge"],
    "Air_Freight": ["Transport_Edge"],
    "Refrigerated_Freight": ["Ocean_Freight"],
})
matches = graph.query(
    'MATCH (n:Transport_Edge) RETURN n.id AS id',
    label_hierarchy=transport,
)
```

Relationship types can be expanded the same way for one query:

```python
from gestaltdb.hierarchy import RelationHierarchy

relations = RelationHierarchy({
    "Ocean_Freight": ["Transport_Edge"],
    "Air_Freight": ["Transport_Edge"],
    "Refrigerated_Freight": ["Ocean_Freight"],
})
matches = graph.query(
    'MATCH (a {id: "warehouse"})-[r:Transport_Edge]->(b) RETURN type(r) AS t',
    relation_hierarchy=relations,
)
```

## Traverse and Sample Typed Relationships

```python
import random

from gestaltdb.sampling import SamplingHop, SamplingPattern

neighbors = graph.neighbors_by_edge_type("drug-1", "binds", direction="out")
edges = graph.edges_by_edge_type("drug-1", "binds", direction="out")
seeds = graph.sample_nodes(5, label="Drug", rng=random.Random(5))

one_hop = graph.sample_neighbors(
    "drug-1",
    "binds",
    direction="out",
    sample_size=5,
    rng=random.Random(7),
)

pattern = SamplingPattern([
    SamplingHop("binds", direction="out", sample_size=5),
    SamplingHop("associated_with", direction="out", sample_size=3),
])
paths = graph.sample_typed_paths(["drug-1"], pattern, rng=random.Random(11))
subgraph = graph.sample_typed_subgraph(["drug-1"], pattern)

print(neighbors, edges, one_hop)
print(subgraph["nodes"].keys(), subgraph["edges"].keys(), paths)
```

Typed traversal depends on `Edge(properties={"type": "..."})`. If a database was populated before typed adjacency existed, run `graph.rebuild_typed_adjacency()`.

Live traversal can expand a parent relation through an explicit hierarchy. The
per-hop `sample_size` remains the total fanout across the expanded union:

```python
from gestaltdb.sampling import RelationHierarchy

transport = RelationHierarchy({
    "Ocean_Freight": ["Transport_Edge"],
    "Air_Freight": ["Transport_Edge"],
})
one_hop = graph.sample_neighbors(
    "warehouse", "Transport_Edge", sample_size=5,
    relation_hierarchy=transport,
)
paths = graph.sample_typed_paths(
    ["warehouse"],
    [{"edge_type": "Transport_Edge", "sample_size": 5}],
    relation_hierarchy=transport,
)
```

## Ingest Arrow Columns

```python
from tempfile import TemporaryDirectory

import pyarrow as pa

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        graph.create_node_property_index("name")
        result = graph.ingest_arrow(
            pa.array(["alice", "bob", "carol"]),
            pa.array(["alice-knows-bob", "bob-knows-carol"]),
            pa.array(["alice", "bob"]),
            pa.array(["bob", "carol"]),
            pa.array(["knows", "knows"]),
            labels=pa.array([["Person"], ["Person"], ["Person"]]),
            node_properties={"name": pa.array(["Alice", "Bob", "Carol"])},
            edge_properties={"since": pa.array([2024, 2025])},
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )
        print(result)
    finally:
        graph.close()
```

## Ingest Polars DataFrames

```python
from tempfile import TemporaryDirectory

import polars as pl

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

nodes = pl.DataFrame({
    "node_id": ["alice", "bob", "carol"],
    "labels": [["Person"], ["Person"], ["Person"]],
    "name": ["Alice", "Bob", "Carol"],
    "age": [34, 36, 29],
})

edges = pl.DataFrame({
    "edge_id": ["alice-knows-bob", "bob-knows-carol"],
    "source": ["alice", "bob"],
    "target": ["bob", "carol"],
    "edge_type": ["knows", "knows"],
    "since": [2024, 2025],
})

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        graph.create_node_property_index("name")
        graph.ingest_polars(
            nodes,
            edges,
            node_property_columns=["name", "age"],
            edge_property_columns=["since"],
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )

        result = graph.query(
            'MATCH (a:Person) MATCH (a)-[:knows]->(b) RETURN a.name, b.name ORDER BY a.name'
        )
        print(result.records)
    finally:
        graph.close()
```

## Build and Use a Sampler Snapshot

Filter a property graph once, then reuse compact adjacency for weighted neighbors,
layers, walks, and edge-seeded batches. Unlike filtering only the seed list,
snapshot filters restrict the graph available to every hop. This example uses
indexed edge IDs as a build-time membership predicate; construction still scans
graph records. No concurrent writer is active during selection/build.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.sampling import ExternalNeighborSamplingSpec, HardNegativeConfig, SamplerEngine
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node(node_id="drug-1", properties={"kind": "drug"}),
            Node(node_id="protein-1", properties={"kind": "protein"}),
            Node(node_id="disease-1", properties={"kind": "disease"}),
        ])
        graph.put_edges_bulk([
            Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "binds", "confidence": 0.9}),
            Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "associated_with", "confidence": 0.7}),
            Edge(edge_id="weak", source="drug-1", target="protein-1", properties={"type": "binds", "confidence": 0.2}),
        ])

        graph.create_edge_property_index("confidence")
        eligible_edges = {
            graph.key_to_string(key)
            for key in graph.iter_edge_ids_by_property_range("confidence", 0.5, None)
        }
        snapshot = graph.build_sampler_snapshot(
            f"{tmpdir}/sampler",
            edge_filter=lambda edge: edge.get_id in eligible_edges,
            edge_weight_property="confidence",
        )
        assert snapshot.num_edges == 2  # the weak edge never enters any sampled hop
        engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=13)

        sample = engine.sample_neighbors_external(
            ["drug-1"], fanout=10, direction="out", relations=["binds"], strategy="weighted"
        )
        print(sample.to_external(snapshot)["neighbor_nodes"].tolist())

        layers = engine.sample_layers_external(
            ["drug-1"], [ExternalNeighborSamplingSpec(fanout=5, relations=["binds"])]
        )
        print(layers.to_external(snapshot)["layers"][0]["neighbor_nodes"].tolist())
        blocks = layers.to_blocks(snapshot)
        print(blocks[0]["src_nodes"].tolist(), blocks[0]["edge_index"].shape)

        walks = engine.sample_random_walks_external(
            ["drug-1"], 2, metapath=["binds", "associated_with"]
        )
        external_walks = walks.to_external(snapshot)
        print(external_walks["node_ids"][0, : walks.lengths[0] + 1].tolist())

        batch = engine.sample_subgraph_external(
            ["d1-p1"],
            fanouts=[10, 5],
            negative_config=HardNegativeConfig(negatives_per_positive=2),
        )
        arrays = batch.to_numpy()
        print(arrays["senders"], arrays["receivers"], arrays["positives"], arrays["negatives"])

        induced = engine.sample_induced_subgraph(2, max_edges=10)
        print(induced.n_nodes, induced.n_edges)
    finally:
        graph.close()
```

Use `SamplerEngine.load(path, mode="memmap")` for large snapshots that should be memory mapped instead of eagerly loaded into RAM.

Balanced draws are opt-in and split the requested quota across the groups
present:

```python
from gestaltdb.sampling import ExternalNeighborSamplingSpec, SamplerEngine

engine = SamplerEngine.load("sampler_snapshot", seed=13)
balanced_neighbors = engine.sample_neighbors([0], 9, balanced=True)
balanced_nodes = engine.sample_nodes(30, balanced=True)
balanced_edges = engine.sample_edges(9, balanced=True)
layer = ExternalNeighborSamplingSpec(fanout=9, balanced=True)
```

Groups are compact node types for `sample_nodes` and relation IDs for
`sample_edges` and `sample_neighbors`. A group smaller than its share returns
fewer rows unless `replace=True`; quota is not borrowed from other groups.

On LMDB, high-level non-temporal builds automatically pin one mutable MVCC
horizon and record `CurrentReadProvenance`. Pass `read_snapshot=True` to require
that guarantee; LevelDB and default non-transactional PyRex reject explicit
requests because they cannot expose one unified verifiable source snapshot.
Read-only Cypher accepts the same option, and `graph.current_read_view()` keeps
several direct graph reads on one horizon.

## Visualize Graphs

GestaltDB ships offline interactive visualization: the D3.js + React front
end is prebuilt and packaged with the library, so saving `.html` artifacts
or rendering inline in Jupyter needs no JavaScript toolchain or network.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import VizOptions, visualize_query

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows"}))

        figure = visualize_query(
            graph,
            'MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b',
            options=VizOptions(title="Knows graph", theme="dark",
                               node_label_property="name", edge_labels="all"),
        )
        figure.save(f"{tmpdir}/knows.html")  # open offline in any browser
        figure.save_svg(f"{tmpdir}/knows.svg", layout="circle")  # no browser needed
        print(repr(figure))
    finally:
        graph.close()
```

`GraphDB.visualize` is the same path as a method, and `visualize_sample`
covers large graphs through typed sampling instead of full dumps:

```python
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.viz.api import VizOptions

pattern = SamplingPattern([SamplingHop("binds", direction="out", sample_size=5)])
figure = graph.visualize(seeds=["drug-1"], pattern=pattern)
figure = graph.visualize(
    'MATCH (a:Person) RETURN a LIMIT 25',
    options=VizOptions(max_nodes=500, max_edges=1000),
)
```

Caps (defaults 2000 nodes / 5000 edges) truncate deterministically with a
`TruncationWarning` and an on-canvas banner; payloads beyond twice the caps
raise `VizCapExceededError` naming the sampling alternative.

`as_svg()` / `save_svg()` use deterministic circle/grid layouts or supplied
`positions={node_id: (x, y)}` coordinates; they render the Python graph rather
than the notebook iframe state. Use `edge_labels="all"` for static edge labels.
Browser SVG/PNG downloads capture the interactive viewport.

The browser also supports all-visible-content exports, transparent backgrounds,
custom dimensions, PNG scale, and saved view-state JSON. Search includes properties;
native-label filters include secondary labels, and focus can follow directed
relationships over 1–5 hops. Attribute mappings such as
`VizOptions(node_size_property="mass", edge_width_property="score")` work in
both the browser and Python SVG output.

## Define Canonical Temporal Values

Temporal values require timezone-aware datetimes, normalize to UTC
microseconds, and use half-open intervals `[start, end)`. These values establish
shared semantics for temporal history, read views, Cypher qualifiers, and
temporal sampler snapshots; they do not make mutable current-state records
temporal.

```python
from datetime import datetime, timedelta, timezone

from gestaltdb.temporal import TemporalContext, TemporalInstant, TemporalInterval

valid_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
valid_to = valid_from + timedelta(days=30)
validity = TemporalInterval.from_values(valid_from, valid_to)

assert TemporalContext.as_of(
    datetime(2026, 1, 15, tzinfo=timezone.utc)
).matches(validity)
assert not TemporalContext.as_of(valid_to).matches(validity)

instant = TemporalInstant.parse("2026-01-15T12:00:00Z")
assert TemporalInstant.decode_sortable(instant.encode_sortable()) == instant
```

## Append Immutable Temporal Versions

Temporal version writes preserve assertions, corrections, and retractions
without changing current-state records returned by `get_node` or `get_edge`.
Exact history, as-of resolution, and typed traversal use separate temporal
indexes.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInterval

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        asserted = graph.put_node_version(
            Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}),
            valid=TemporalInterval.parse("2020-01-01T00:00:00Z"),
            metadata={"source": "people-import"},
        )
        corrected = graph.correct_node_version(
            Node(node_id="alice", labels=["Person"], properties={"name": "Alicia"}),
            supersedes_version_id=asserted.version_id,
        )
        graph.retract_node_version(
            "alice",
            valid_from="2030-01-01T00:00:00Z",
            supersedes_version_id=corrected.version_id,
            reason="source withdrew the assertion",
        )

        history = list(graph.iter_node_versions("alice"))
        assert [version.operation.value for version in history] == [
            "assert", "correct", "retract"
        ]
        assert graph.get_node(b"alice") is None

        at_2025 = graph.get_node_as_of(
            "alice", valid_time="2025-01-01T00:00:00Z"
        )
        assert at_2025.node.properties["name"] == "Alicia"
    finally:
        graph.close()
```

## Migrate Legacy `TimeIndexedEdge` Records

`TimeIndexedEdge` is a deprecated timestamp-prefixed current-state layout. Back
up and quiesce the database, migrate without deletion, validate temporal reads,
then run cleanup. The example creates legacy data only to demonstrate the
migration; new code should call `put_edge_version` directly.

```python
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB, TimeIndexedEdge
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        first = datetime(2024, 1, 1, tzinfo=timezone.utc)
        graph.put_edge(TimeIndexedEdge(
            first, edge_id="employment", source="alice", target="acme",
            properties={"type": "WORKS_FOR", "revision": 1},
        ), update_adjacency=False)
        graph.put_edge(TimeIndexedEdge(
            first + timedelta(days=30), edge_id="employment",
            source="alice", target="acme",
            properties={"type": "WORKS_FOR", "revision": 2},
        ), update_adjacency=False)

        migrated = graph.migrate_time_indexed_edges(delete_legacy=False)
        assert len(migrated) == 2
        assert graph.get_edge_as_of(
            "employment", valid_time="2024-01-15T00:00:00Z"
        ).edge.properties["revision"] == 1

        assert graph.migrate_time_indexed_edges(delete_legacy=True) == ()
        assert len(list(graph.iter_edge_versions("employment"))) == 2
    finally:
        graph.close()
```
