"""Columnar node ingestion cores (Phase 3 extraction, ingestion side).

Chunked Arrow/Polars node writes with per-chunk secondary-index upkeep and
progress reporting. The transaction boundary stays on ``GraphDB``: wrappers
open a transaction when required and delegate the store-bound scan here.
Entity-column variants build serialized payloads, then reuse the Arrow core
through the transaction-aware ``GraphDB.ingest_nodes_arrow`` wrapper passed
in as ``ingest_arrow_fn``, preserving nested-transaction behavior exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..ingestion import IndexMaintenanceMode, NodeList
from ..serializers import JSONSerializer
from . import index_maintenance as _index_maintenance
from . import ingest_support as _ingest_support
from .entities import Node


def ingest_nodes_arrow(store: KVStore, entity_serializer, indexed_node_properties,
                       stale_key: bytes, node_ids, node_values, *,
                       native: bool = True, chunk_size: int = 100_000,
                       append_only: bool = False, index_mode: str = "maintain",
                       progress: bool = False) -> int:
    """Ingest attributed nodes from Arrow-like columns of IDs and payloads."""
    _ingest_support.validate_index_mode(index_mode)
    index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    node_list = NodeList.from_arrow(node_ids, node_values)
    with _ingest_support.ingestion_progress(progress, total=len(node_list.node_ids), desc="ingest nodes") as progress_bar:
        for chunk in node_list.chunks(chunk_size):
            if index_mode == "maintain" and not append_only:
                _ingest_support.delete_existing_node_indexes_for_columnar_chunk(
                    store, entity_serializer, indexed_node_properties, chunk)
            store.ingest_nodes_columnar(chunk, native=native)
            if index_mode == "maintain":
                _ingest_support.put_node_indexes_for_columnar_chunk(
                    store, entity_serializer, indexed_node_properties, chunk)
            if progress_bar is not None:
                progress_bar.update(len(chunk.node_ids))
    if index_mode == "defer":
        _index_maintenance.mark_indexes_stale(store, stale_key, "node_label")
        if indexed_node_properties:
            _index_maintenance.mark_indexes_stale(store, stale_key, "node_property")
    return len(node_list.node_ids)


def ingest_nodes_polars(store: KVStore, entity_serializer, indexed_node_properties,
                        stale_key: bytes, df, *, node_id: str = "node_id",
                        node_value: str = "node_value", native: bool = True,
                        chunk_size: int = 100_000, append_only: bool = False,
                        index_mode: str = "maintain", progress: bool = False) -> int:
    """Ingest attributed nodes from a Polars DataFrame of payloads."""
    try:
        import polars as pl
    except ImportError as exc:
        from ..ingestion import _missing_dependency_error

        raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_nodes_polars") from exc
    _ingest_support.validate_index_mode(index_mode)
    index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    _ingest_support.validate_polars_frame(pl, df, "GraphDB.ingest_nodes_polars")
    total_rows = _ingest_support.polars_row_count(pl, df)
    total = 0
    with _ingest_support.ingestion_progress(progress, total=total_rows, desc="ingest nodes") as progress_bar:
        for df_chunk in _ingest_support.iter_polars_chunks(pl, df, chunk_size):
            node_list = NodeList.from_polars(df_chunk, node_id=node_id, node_value=node_value)
            for chunk in node_list.chunks(chunk_size):
                if index_mode == "maintain" and not append_only:
                    _ingest_support.delete_existing_node_indexes_for_columnar_chunk(
                        store, entity_serializer, indexed_node_properties, chunk)
                store.ingest_nodes_columnar(chunk, native=native)
                if index_mode == "maintain":
                    _ingest_support.put_node_indexes_for_columnar_chunk(
                        store, entity_serializer, indexed_node_properties, chunk)
                if progress_bar is not None:
                    progress_bar.update(len(chunk.node_ids))
            total += len(node_list.node_ids)
    if index_mode == "defer":
        _index_maintenance.mark_indexes_stale(store, stale_key, "node_label")
        if indexed_node_properties:
            _index_maintenance.mark_indexes_stale(store, stale_key, "node_property")
    return total


def ingest_nodes_polars_entities(store: KVStore, entity_serializer, raw_serializer,
                                 ingest_arrow_fn: Callable, df, *,
                                 node_id: str = "node_id", labels=None,
                                 property_columns=None, native: bool = True,
                                 chunk_size: int = 100_000, append_only: bool = False,
                                 index_mode: str = "maintain",
                                 progress: bool = False) -> int:
    """Ingest node entity columns from a Polars DataFrame.

    With a JSON serializer, payloads are built with Polars struct JSON
    encoding; other serializers fall back to Python ``Node`` serialization.
    """
    try:
        import polars as pl
    except ImportError as exc:
        from ..ingestion import _missing_dependency_error

        raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_nodes_polars_entities") from exc
    _ingest_support.validate_polars_frame(pl, df, "GraphDB.ingest_nodes_polars_entities")
    columns = _ingest_support.polars_columns(pl, df)
    if node_id not in columns:
        raise ValueError(f"missing required columns: {node_id}")

    if property_columns is None:
        excluded = {node_id}
        if isinstance(labels, str):
            excluded.add(labels)
        property_columns = [column for column in columns if column not in excluded]
    missing = [column for column in property_columns if column not in columns]
    if missing:
        raise ValueError(f"missing property columns: {', '.join(missing)}")

    total = 0
    total_rows = _ingest_support.polars_row_count(pl, df)
    with _ingest_support.ingestion_progress(progress, total=total_rows, desc="ingest nodes") as progress_bar:
        for df_chunk in _ingest_support.iter_polars_chunks(pl, df, chunk_size):
            if isinstance(raw_serializer, JSONSerializer):
                label_expr = _ingest_support.polars_labels_expr(pl, df_chunk, labels)
                value_expr = pl.struct(
                    [
                        pl.col(node_id).alias("id"),
                        _ingest_support.polars_properties_expr(pl, property_columns).alias("properties"),
                        label_expr.alias("labels"),
                    ]
                ).struct.json_encode()
                payloads = df_chunk.select(value_expr.alias("node_value"))["node_value"].to_arrow().cast("binary")
            else:
                payloads = []
                selected = [node_id, *property_columns]
                label_column = labels if isinstance(labels, str) and labels in df_chunk.columns else None
                if label_column is not None:
                    selected.append(label_column)
                for row in df_chunk.select(selected).rows(named=True):
                    node_labels = row[label_column] if label_column is not None else ([] if labels is None or isinstance(labels, str) else labels)
                    if isinstance(node_labels, str):
                        node_labels = [node_labels]
                    payloads.append(
                        entity_serializer.serialize(
                            Node(
                                node_id=row[node_id],
                                labels=node_labels,
                                properties={column: row[column] for column in property_columns},
                            ),
                            "Node",
                        )
                    )

            total += ingest_arrow_fn(
                df_chunk[node_id].to_arrow(),
                payloads,
                native=native,
                chunk_size=chunk_size,
                append_only=append_only,
                index_mode=index_mode,
                progress=False,
            )
            if progress_bar is not None:
                progress_bar.update(df_chunk.height)
    return total


def ingest_nodes_arrow_entities(store: KVStore, entity_serializer, raw_serializer,
                                ingest_arrow_fn: Callable, ingest_polars_entities_fn: Callable,
                                node_ids, *, labels=None, properties=None,
                                native: bool = True, chunk_size: int = 100_000,
                                append_only: bool = False, index_mode: str = "maintain",
                                progress: bool = False) -> int:
    """Ingest node entity columns from Arrow-like columns."""
    try:
        import polars as pl
    except ImportError:
        pl = None

    if isinstance(raw_serializer, JSONSerializer) and pl is not None:
        data = {"node_id": node_ids}
        if labels is not None:
            data["labels"] = labels
        for name, column in (properties or {}).items():
            data[name] = column
        return ingest_polars_entities_fn(
            pl.DataFrame(data),
            node_id="node_id",
            labels="labels" if labels is not None else None,
            property_columns=list((properties or {}).keys()),
            native=native,
            chunk_size=chunk_size,
            append_only=append_only,
            index_mode=index_mode,
            progress=progress,
        )

    raw_node_ids = node_ids.to_pylist() if hasattr(node_ids, "to_pylist") else list(node_ids)
    raw_labels = labels.to_pylist() if hasattr(labels, "to_pylist") else labels
    raw_properties = {
        name: column.to_pylist() if hasattr(column, "to_pylist") else list(column)
        for name, column in (properties or {}).items()
    }
    payloads = []
    for index, node_key in enumerate(raw_node_ids):
        node_labels = raw_labels[index] if isinstance(raw_labels, list) else raw_labels
        if isinstance(node_labels, str):
            node_labels = [node_labels]
        payloads.append(
            entity_serializer.serialize(
                Node(
                    node_id=node_key,
                    labels=node_labels,
                    properties={name: values[index] for name, values in raw_properties.items()},
                ),
                "Node",
            )
        )
    return ingest_arrow_fn(
        raw_node_ids,
        payloads,
        native=native,
        chunk_size=chunk_size,
        append_only=append_only,
        index_mode=index_mode,
        progress=progress,
    )
