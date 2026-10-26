"""Format-specific columnar ingestion over explicit current-graph sessions.

Arrow and Polars loaders retain native backend batch paths. Entity conversion
callbacks bind to the same write scope, never to GraphDB or a parent store.
Low-level methods retain maintain/defer semantics; graph-wide orchestration
and its defer/rebuild policy remain separate from these format adapters.
"""

from ._graph import index_maintenance as _index_maintenance
from ._graph import ingest_edges, ingest_nodes
from ._graph import ingest_support as _support
from .ingestion import ColumnarIngestionMode, IndexMaintenanceMode
from .indexes import IndexService, STALE_KEY


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
        self._session = session
        self.arrow = ArrowIngestor(session)
        self.polars = PolarsIngestor(session)


class IngestionCoordinator(IngestionService):
    """Graph-wide Arrow/Polars loading plus deferred-index coordination.

    The coordinator owns mode dispatch across node and edge families and the
    optional aggregate rebuild. It requires only a ``StorageSession``; callers
    that need cross-family atomicity run it inside a transaction. A temporal
    capability is optional and used only to detect or rebuild temporal
    staleness. Without one, temporal index state is neither inferred nor
    modified, and a persisted temporal rebuild request fails closed.
    """

    def __init__(self, session, *, temporal=None):
        super().__init__(session)
        self._temporal = temporal

    def stale_indexes(self) -> tuple[str, ...]:
        """Return persisted stale families plus derived temporal staleness."""
        stale = set(_index_maintenance.load_stale_indexes(
            self._session.store, STALE_KEY))
        if (
            self._temporal is not None
            and self._temporal.index_horizon() is None
            and self._temporal.has_visible_history()
        ):
            stale.add("temporal")
        return tuple(sorted(stale))

    def rebuild_deferred_indexes(self) -> dict[str, int]:
        """Rebuild only the index families marked stale by deferred loading."""
        stale = set(self.stale_indexes())
        indexes = IndexService(self._session)
        rebuilt: dict[str, int] = {}
        if stale.intersection({"node_label", "node_property"}):
            rebuilt.update(indexes.rebuild_node_indexes(
                labels="node_label" in stale,
                properties=(self._session.indexed_node_properties
                            if "node_property" in stale else set()),
            ))
        if stale.intersection({"edge_type", "edge_property"}):
            rebuilt.update(indexes.rebuild_edge_indexes(
                edge_types="edge_type" in stale,
                properties=(self._session.indexed_edge_properties
                            if "edge_property" in stale else set()),
            ))
        if "temporal" in stale:
            if self._temporal is None:
                raise RuntimeError(
                    "temporal index rebuild requires a temporal capability")
            rebuilt.update(self._temporal.rebuild_temporal_indexes())
        return rebuilt

    def _result(self, node_count: int, edge_count: int,
                index_mode) -> dict[str, object]:
        rebuilt = (
            self.rebuild_deferred_indexes()
            if _support.should_rebuild_after_ingest(index_mode) else {}
        )
        return {
            "nodes": node_count,
            "edges": edge_count,
            "rebuilt_indexes": rebuilt,
            "stale_indexes": self.stale_indexes(),
        }

    def ingest_polars(
        self,
        node_df,
        edge_df,
        *,
        ingestion_mode=ColumnarIngestionMode.ENTITY_COLUMNS,
        index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        node_id: str = "node_id",
        node_value: str = "node_value",
        labels="labels",
        node_property_columns=None,
        edge_id: str = "edge_id",
        source: str = "source",
        target: str = "target",
        edge_type: str = "edge_type",
        edge_value: str = "edge_value",
        edge_property_columns=None,
        native: bool = True,
        chunk_size: int = 100_000,
        progress: bool = False,
    ) -> dict[str, object]:
        """Load Polars node/edge frames and apply the index policy."""
        mode = _support.coerce_columnar_ingestion_mode(ingestion_mode)
        low_level_index_mode = _support.low_level_index_mode(index_mode)
        if mode == ColumnarIngestionMode.ENTITY_COLUMNS.value:
            node_count = self.polars.node_entities(
                node_df, node_id=node_id, labels=labels,
                property_columns=node_property_columns, native=native,
                chunk_size=chunk_size, append_only=True,
                index_mode=low_level_index_mode, progress=progress)
            edge_count = self.polars.edge_entities(
                edge_df, edge_id=edge_id, source=source, target=target,
                edge_type=edge_type, property_columns=edge_property_columns,
                append_only=True, native=native, chunk_size=chunk_size,
                index_mode=low_level_index_mode, progress=progress)
        else:
            node_count = self.polars.nodes(
                node_df, node_id=node_id, node_value=node_value, native=native,
                chunk_size=chunk_size, append_only=True,
                index_mode=low_level_index_mode, progress=progress)
            edge_count = self.polars.edges(
                edge_df, edge_id=edge_id, source=source, target=target,
                edge_type=edge_type, edge_value=edge_value, append_only=True,
                native=native, chunk_size=chunk_size,
                index_mode=low_level_index_mode, progress=progress)
        return self._result(node_count, edge_count, index_mode)

    def ingest_arrow(
        self,
        node_ids,
        edge_ids,
        sources,
        targets,
        edge_types,
        *,
        ingestion_mode=ColumnarIngestionMode.ENTITY_COLUMNS,
        index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        node_values=None,
        edge_values=None,
        labels=None,
        node_properties=None,
        edge_properties=None,
        native: bool = True,
        chunk_size: int = 100_000,
        progress: bool = False,
    ) -> dict[str, object]:
        """Load Arrow-like node/edge columns and apply the index policy."""
        mode = _support.coerce_columnar_ingestion_mode(ingestion_mode)
        low_level_index_mode = _support.low_level_index_mode(index_mode)
        if mode == ColumnarIngestionMode.ENTITY_COLUMNS.value:
            node_count = self.arrow.node_entities(
                node_ids, labels=labels, properties=node_properties,
                native=native, chunk_size=chunk_size, append_only=True,
                index_mode=low_level_index_mode, progress=progress)
            edge_count = self.arrow.edge_entities(
                edge_ids, sources, targets, edge_types,
                properties=edge_properties, append_only=True, native=native,
                chunk_size=chunk_size, index_mode=low_level_index_mode,
                progress=progress)
        else:
            if node_values is None or edge_values is None:
                raise ValueError(
                    "node_values and edge_values are required for serialized "
                    "payload ingestion")
            node_count = self.arrow.nodes(
                node_ids, node_values, native=native, chunk_size=chunk_size,
                append_only=True, index_mode=low_level_index_mode,
                progress=progress)
            edge_count = self.arrow.edges(
                edge_ids, sources, targets, edge_types, edge_values,
                append_only=True, native=native, chunk_size=chunk_size,
                index_mode=low_level_index_mode, progress=progress)
        return self._result(node_count, edge_count, index_mode)
