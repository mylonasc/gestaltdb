"""Format-specific columnar ingestion over explicit current-graph sessions.

Arrow and Polars loaders retain native backend batch paths. Entity conversion
callbacks bind to the same write scope, never to GraphDB or a parent store.
Low-level methods retain maintain/defer semantics; graph-wide orchestration
and its defer/rebuild policy remain separate from these format adapters.
"""

from ._graph import ingest_edges, ingest_nodes
from .indexes import STALE_KEY


class ArrowIngestor:
    """Arrow-like columns (including Python columns), entities or payloads."""

    def __init__(self, session):
        self._session = session

    def nodes(self, node_ids, node_values, **options):
        """Write serialized node payload columns in one scoped batch operation."""
        with self._session.write_scope() as s:
            return ingest_nodes.ingest_nodes_arrow(
                s.store, s.entity_serializer, s.indexed_node_properties,
                STALE_KEY, node_ids, node_values, **options)

    def edges(self, edge_ids, sources, targets, edge_types, edge_values, **options):
        """Write serialized edge columns, maintaining typed adjacency."""
        with self._session.write_scope() as s:
            return ingest_edges.ingest_edges_arrow(
                s.store, s.entity_serializer, s.indexed_edge_properties,
                STALE_KEY, s.adjacency_counts,
                edge_ids, sources, targets, edge_types, edge_values, **options)

    def node_entities(self, node_ids, **options):
        """Build node payloads from entity columns without facade callbacks."""
        with self._session.write_scope() as s:
            return ingest_nodes.ingest_nodes_arrow_entities(
                s.store, s.entity_serializer, s.serializer,
                ArrowIngestor(s).nodes, PolarsIngestor(s).node_entities,
                node_ids, **options)

    def edge_entities(self, edge_ids, sources, targets, edge_types, **options):
        """Build edge payloads from entity columns in the exact write scope."""
        with self._session.write_scope() as s:
            return ingest_edges.ingest_edges_arrow_entities(
                s.store, s.entity_serializer, s.serializer,
                s.indexed_edge_properties, STALE_KEY, s.adjacency_counts,
                ArrowIngestor(s).edges, PolarsIngestor(s).edge_entities,
                edge_ids, sources, targets, edge_types, **options)


class PolarsIngestor:
    """Eager/lazy Polars frames, specialized without importing Polars eagerly."""

    def __init__(self, session):
        self._session = session

    def nodes(self, df, **options):
        """Write a frame containing serialized node payloads."""
        with self._session.write_scope() as s:
            return ingest_nodes.ingest_nodes_polars(
                s.store, s.entity_serializer, s.indexed_node_properties,
                STALE_KEY, df, **options)

    def edges(self, df, **options):
        """Write a frame containing serialized edges and typed adjacency."""
        with self._session.write_scope() as s:
            return ingest_edges.ingest_edges_polars(
                s.store, s.entity_serializer, s.indexed_edge_properties,
                STALE_KEY, s.adjacency_counts, df, **options)

    def node_entities(self, df, **options):
        """Encode structured node columns, preserving the optimized JSON path."""
        with self._session.write_scope() as s:
            return ingest_nodes.ingest_nodes_polars_entities(
                s.store, s.entity_serializer, s.serializer,
                ArrowIngestor(s).nodes, df, **options)

    def edge_entities(self, df, **options):
        """Encode structured edge columns without calling a graph facade."""
        with self._session.write_scope() as s:
            return ingest_edges.ingest_edges_polars_entities(
                s.store, s.entity_serializer, s.serializer,
                s.indexed_edge_properties, STALE_KEY, s.adjacency_counts,
                ArrowIngestor(s).edges, df, **options)


class IngestionService:
    """Format-specific loaders sharing one borrowed StorageSession scope."""

    def __init__(self, session):
        self.arrow = ArrowIngestor(session)
        self.polars = PolarsIngestor(session)
