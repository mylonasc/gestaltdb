# Secondary Indexing & Range Queries

**When to read:** Read this file when you are working on node labels, relationship types, property index maintenance, range lookups, index serialization, or deferred indexing rebuilds.

---

## 1. Indexing Architecture

GestaltDB separates indexes into two categories:

### Automatic Indexes
- **Node Labels:**
  Labels are passed via `Node(labels=[...])` and stored deduplicated. GestaltDB automatically indexes labels on `put_node`, `put_nodes`, and columnar ingestion.
  Lookup helper: `graph.nodes_by_label("Person")`.
- **Relationship Types:**
  When edges include `Edge(properties={"type": "rel_name"})`, GestaltDB indexes typed adjacency records (`A:<type>:<dir>:<node_id>`) automatically.
  Lookup helpers: `graph.neighbors_by_edge_type(node_id, edge_type, direction="out")`, `graph.edges_by_edge_type(...)`.

### Explicit Secondary Property Indexes
Property indexes are **not** created by default. They must be registered before being used for index-accelerated lookups:
- `graph.create_node_property_index("name")`
- `graph.create_edge_property_index("weight")`

Metadata about active indexes is persisted in store metadata (`_node_property_indexes_metadata`, `_edge_property_indexes_metadata`) and preserved across store reopens.

---

## 2. Lookup & Range Query APIs

### Exact Match Lookups
- `graph.nodes_by_property(prop_name, value)`
- `graph.nodes_by_label_property(label, prop_name, value)`
- `graph.edges_by_property(prop_name, value)`
- `graph.edges_by_type_property(edge_type, prop_name, value)`

### Range Queries
For ordered comparisons (`<`, `<=`, `>`, `>=`):
- `graph.nodes_by_property_range(prop_name, min_val, max_val)`
- `graph.nodes_by_label_property_range(label, prop_name, min_val, max_val)`
- `graph.edges_by_property_range(prop_name, min_val, max_val)`
- `graph.edges_by_type_property_range(edge_type, prop_name, min_val, max_val)`

Pass `None` for unbounded ends (e.g. `start_value=10.0, end_value=None` for `>= 10.0`).

Range bounds default inclusive. Set `include_start=False` or
`include_end=False` for exclusive bounds.

### Filtering Cohorts for Repeated Sampling

Use the corresponding `iter_node_ids_by_*` / `iter_edge_ids_by_*` methods when
only IDs are needed. They return bytes without hydrating entities. Property
index creation also maintains label/property or type/property composite entries;
these are not arbitrary multi-property indexes. Eligible Cypher predicates use
indexed candidate selection plus residual checks, not universal index coverage.

Filtering seed IDs does not constrain later traversal. Build a property-defined
snapshot with `node_filter`/`edge_filter`, then reuse `SamplerEngine`. The builder
still scans source records; indexed ID sets passed to callbacks do not push
those indexes into the builder. See `docs/property-filtering.rst`.

Immutable GestaltDB temporal scalars are valid exact and range index values.
Their type-prefixed encodings preserve same-category chronological order;
fixed-offset `TemporalTime` values use UTC time for equality and ordering.

---

## 3. Index Maintenance & Rebuilding

When bulk ingesting data using `IndexMaintenanceMode.DEFER`, secondary indexes are bypassed during the streaming phase to maximize write throughput.

Before executing queries relying on property indexes:
- Call `graph.rebuild_deferred_indexes()` to bring secondary indexes up to date.
- If migrating older stores created without typed adjacency, call `graph.rebuild_typed_adjacency()`.

---

## 4. Runnable Examples

### Creating Property Indexes and Executing Range Queries
```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/index_demo"), JSONSerializer())
    try:
        # Register property index
        graph.create_node_property_index("score")
        graph.create_edge_property_index("confidence")

        # Insert nodes
        graph.put_nodes([
            Node(node_id="gene-1", labels=["Gene"], properties={"score": 0.42}),
            Node(node_id="gene-2", labels=["Gene"], properties={"score": 0.89}),
            Node(node_id="gene-3", labels=["Gene"], properties={"score": 0.95}),
        ])

        # Insert edge
        graph.put_edge(Edge(
            edge_id="e1",
            source="gene-2",
            target="gene-3",
            properties={"type": "interacts_with", "confidence": 0.99},
        ))

        # Query by label and range
        high_scorers = graph.nodes_by_label_property_range("Gene", "score", 0.8, None)
        print("High scorers:", [n.get_id for n in high_scorers])

        # Edge range query by type
        strong_edges = graph.edges_by_type_property_range("interacts_with", "confidence", 0.9, None)
        print("Strong edges:", [e.get_id for e in strong_edges])
    finally:
        graph.close()
```
