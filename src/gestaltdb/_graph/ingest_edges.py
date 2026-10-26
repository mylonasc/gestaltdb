"""Columnar edge ingestion cores (Phase 3 extraction, ingestion side).

Bulk edge writes, Arrow/Polars payload paths, and entity-column variants.
The transaction boundary stays on ``GraphDB``: wrappers open a transaction
when required and delegate the store-bound batch work here. Entity-column
variants reuse the transaction-aware Arrow/polars-entity wrappers passed in
as callables, preserving nested-transaction behavior exactly.

Columnar edge ingestion is append-only and maintains typed adjacency but
intentionally skips legacy adjacency blobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..ingestion import EdgeList, IndexMaintenanceMode
from ..serializers import JSONSerializer
from . import adjacency as _adjacency
from . import index_maintenance as _index_maintenance
from . import ingest_support as _ingest_support
from .edge_records import get_edge, remove_edge_from_endpoint_adjacencies
from .entities import (
    Edge,
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
    key_to_string,
    node_key_to_bytes,
)


def put_edges_bulk(store: KVStore, entity_serializer, raw_serializer,
                   indexed_edge_properties, adj_cache: dict,
                   edges: list, check_existing: bool = True) -> None:
    """Store multiple edges and update adjacency and secondary indexes."""
    # 1) Build a dict[edge_id, bytes] to store all edges in one go
    edge_dict = {}
    typed_adjacency_records = []
    index_entries = []
    range_entries = []
    # 2) Accumulate adjacency changes in memory: node_id -> set(edge_ids)
    adjacency_accumulator = {}  # the keys are "nodes" and the values are sets of edges for where the nodes appear as source or destinations (separately).

    for e in edges:
        if check_existing:
            old_edge = get_edge(store, entity_serializer, e.get_id_bytes)
            if old_edge is not None:
                _adjacency.delete_typed_adjacency_for_edge(store, adj_cache, old_edge)
                _index_maintenance.delete_edge_indexes(store, indexed_edge_properties, old_edge)
                remove_edge_from_endpoint_adjacencies(store, raw_serializer, old_edge)
        e_bytes = entity_serializer.serialize(e, 'Edge')
        _source = node_key_to_bytes(e.source)
        _target = node_key_to_bytes(e.target)
        edge_dict[e.get_id_bytes] = e_bytes
        edge_type = e.get_type
        if edge_type is not None:
            typed_adjacency_records.append((_source, _target, edge_type, e.get_id_bytes))
            index_entries.append(("edge_type", [str(edge_type).encode("utf-8")], e.get_id_bytes))
        for property_name in indexed_edge_properties:
            if property_name in e.properties:
                property_value = _property_value_to_index_bytes(e.properties[property_name])
                index_entries.append((
                    "edge_property",
                    [property_name.encode("utf-8"), property_value],
                    e.get_id_bytes,
                ))
                if edge_type is not None:
                    index_entries.append((
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                        e.get_id_bytes,
                    ))
                range_value = _property_value_to_range_index_bytes(e.properties[property_name])
                if range_value is not None:
                    range_entries.append((
                        "edge_property",
                        [property_name.encode("utf-8")],
                        range_value,
                        e.get_id_bytes,
                    ))
                    if edge_type is not None:
                        range_entries.append((
                            "edge_type_property",
                            [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
                            range_value,
                            e.get_id_bytes,
                        ))
        # adjacency accum update
        adjacency_accumulator.setdefault(_source, {'target': [], 'source': []})
        adjacency_accumulator[_source]['source'].append(e.get_id)
        adjacency_accumulator.setdefault(_target, {'target': [], 'source': []})
        adjacency_accumulator[_target]['target'].append(e.get_id)

    # 3) Use the store's put_edges_bulk
    store.put_edges_bulk(edge_dict)
    store.put_typed_adjacency_bulk(typed_adjacency_records)
    _adjacency.update_count_cache(adj_cache, typed_adjacency_records, 1)
    if index_entries:
        store.put_index_entries_bulk(index_entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)

    # 4) Build adjacency dict so we do one read+write per node
    #    node_id -> final adjacency (existing + new edges)
    final_adjacency = {}

    # For each node in adjacency_accumulator, fetch old adjacency,
    # union with new edges, and store in final_adjacency dict
    for node_id, new_edges_source_target in adjacency_accumulator.items():
        raw_adj = store.get_adjacency(node_id)
        if raw_adj is None:
            old_edges = {'source': set(), 'target': set()}
        else:
            old_edges = raw_serializer.deserialize(raw_adj)
            if 'target' not in old_edges:
                old_edges['target'] = set()

            if 'source' not in old_edges:
                old_edges['source'] = set()
        source_edges = old_edges['source']
        target_edges = old_edges['target']
        if 'source' in new_edges_source_target:
            source_edges = set(source_edges).union(new_edges_source_target['source'])
        if 'target' in new_edges_source_target:
            target_edges = set(target_edges).union(new_edges_source_target['target'])
        # Match put_edge's portable string-ID format, including adjacency
        # written by older bulk writers that used bytes with binary serializers.
        new_adj_value = {
            'source': sorted({key_to_string(edge_id) for edge_id in source_edges}),
            'target': sorted({key_to_string(edge_id) for edge_id in target_edges}),
        }
        final_adjacency[node_id] = entity_serializer.serialize(new_adj_value, 'AdjacencyList')

    # 5) One batch write for adjacency
    store.put_adjacency_bulk(final_adjacency)


def put_edge_property_indexes_for_columnar_chunk(store: KVStore, entity_serializer,
                                                 indexed_edge_properties,
                                                 chunk: EdgeList) -> None:
    """Maintain configured edge property indexes for columnar edges."""
    if not indexed_edge_properties:
        return
    entries = []
    range_entries = []
    for edge_value in chunk.edge_values:
        edge = entity_serializer.deserialize(edge_value, "Edge")
        edge_type = edge.get_type
        for property_name in indexed_edge_properties:
            if property_name in edge.properties:
                raw_value = edge.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append((
                    "edge_property",
                    [property_name.encode("utf-8"), property_value],
                    edge.get_id_bytes,
                ))
                if edge_type is not None:
                    entries.append((
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                        edge.get_id_bytes,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge.get_id_bytes))
                    if edge_type is not None:
                        range_entries.append(("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge.get_id_bytes))
    if entries:
        store.put_index_entries_bulk(entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)


def ingest_edges_arrow(store: KVStore, entity_serializer, indexed_edge_properties,
                       stale_key: bytes, adj_cache: dict,
                       edge_ids, sources, targets, edge_types, edge_values, *,
                       append_only: bool = True, native: bool = True,
                       chunk_size: int = 100_000, index_mode: str = "maintain",
                       progress: bool = False) -> int:
    """Ingest typed edges from Arrow-like columns of IDs, endpoints, and payloads."""
    if not append_only:
        raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
    _ingest_support.validate_index_mode(index_mode)
    index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    edge_list = EdgeList.from_arrow(edge_ids, sources, targets, edge_types, edge_values)
    with _ingest_support.ingestion_progress(progress, total=len(edge_list.edge_ids), desc="ingest edges") as progress_bar:
        for chunk in edge_list.chunks(chunk_size):
            store.ingest_edges_columnar(
                chunk,
                append_only=append_only,
                native=native,
                maintain_indexes=index_mode == "maintain",
            )
            _adjacency.update_count_cache(
                adj_cache,
                zip(chunk.sources, chunk.targets, chunk.edge_types, chunk.edge_ids),
                1,
            )
            if index_mode == "maintain":
                put_edge_property_indexes_for_columnar_chunk(
                    store, entity_serializer, indexed_edge_properties, chunk)
            if progress_bar is not None:
                progress_bar.update(len(chunk.edge_ids))
    if index_mode == "defer":
        _index_maintenance.mark_indexes_stale(store, stale_key, "edge_type")
        if indexed_edge_properties:
            _index_maintenance.mark_indexes_stale(store, stale_key, "edge_property")
    return len(edge_list.edge_ids)


def ingest_edges_polars(store: KVStore, entity_serializer, indexed_edge_properties,
                        stale_key: bytes, adj_cache: dict, df, *,
                        edge_id: str = "edge_id", source: str = "source",
                        target: str = "target", edge_type: str = "edge_type",
                        edge_value: str = "edge_value", append_only: bool = True,
                        native: bool = True, chunk_size: int = 100_000,
                        index_mode: str = "maintain", progress: bool = False) -> int:
    """Ingest typed edges from a Polars DataFrame of payloads."""
    if not append_only:
        raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
    try:
        import polars as pl
    except ImportError as exc:
        from ..ingestion import _missing_dependency_error

        raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_edges_polars") from exc
    _ingest_support.validate_index_mode(index_mode)
    index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    _ingest_support.validate_polars_frame(pl, df, "GraphDB.ingest_edges_polars")
    total_rows = _ingest_support.polars_row_count(pl, df)
    total = 0
    with _ingest_support.ingestion_progress(progress, total=total_rows, desc="ingest edges") as progress_bar:
        for df_chunk in _ingest_support.iter_polars_chunks(pl, df, chunk_size):
            edge_list = EdgeList.from_polars(
                df_chunk,
                edge_id=edge_id,
                source=source,
                target=target,
                edge_type=edge_type,
                edge_value=edge_value,
            )
            for chunk in edge_list.chunks(chunk_size):
                store.ingest_edges_columnar(
                    chunk,
                    append_only=append_only,
                    native=native,
                    maintain_indexes=index_mode == "maintain",
                )
                if index_mode == "maintain":
                    put_edge_property_indexes_for_columnar_chunk(
                        store, entity_serializer, indexed_edge_properties, chunk)
                if progress_bar is not None:
                    progress_bar.update(len(chunk.edge_ids))
            total += len(edge_list.edge_ids)
    if index_mode == "defer":
        _index_maintenance.mark_indexes_stale(store, stale_key, "edge_type")
        if indexed_edge_properties:
            _index_maintenance.mark_indexes_stale(store, stale_key, "edge_property")
    return total


def ingest_edges_polars_entities(store: KVStore, entity_serializer, raw_serializer,
                                 indexed_edge_properties, stale_key: bytes,
                                 adj_cache: dict, ingest_arrow_fn: Callable, df, *,
                                 edge_id: str = "edge_id", source: str = "source",
                                 target: str = "target", edge_type: str = "edge_type",
                                 property_columns=None, append_only: bool = True,
                                 native: bool = True, chunk_size: int = 100_000,
                                 index_mode: str = "maintain",
                                 progress: bool = False) -> int:
    """Ingest edge entity columns from a Polars DataFrame.

    With a JSON serializer, payloads are built with Polars struct JSON
    encoding; other serializers fall back to Python ``Edge`` serialization.
    """
    try:
        import polars as pl
    except ImportError as exc:
        from ..ingestion import _missing_dependency_error

        raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_edges_polars_entities") from exc
    _ingest_support.validate_polars_frame(pl, df, "GraphDB.ingest_edges_polars_entities")
    columns = _ingest_support.polars_columns(pl, df)
    required = [edge_id, source, target, edge_type]
    missing = [column for column in required if column not in columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    if property_columns is None:
        excluded = set(required)
        property_columns = [column for column in columns if column not in excluded]
    missing = [column for column in property_columns if column not in columns]
    if missing:
        raise ValueError(f"missing property columns: {', '.join(missing)}")

    total = 0
    total_rows = _ingest_support.polars_row_count(pl, df)
    with _ingest_support.ingestion_progress(progress, total=total_rows, desc="ingest edges") as progress_bar:
        for df_chunk in _ingest_support.iter_polars_chunks(pl, df, chunk_size):
            if isinstance(raw_serializer, JSONSerializer):
                property_exprs = [pl.col(edge_type).alias("type")]
                property_exprs.extend(pl.col(column) for column in property_columns if column != "type")
                value_expr = pl.struct(
                    [
                        pl.col(edge_id).alias("id"),
                        pl.col(source).alias("source"),
                        pl.col(target).alias("target"),
                        pl.struct(property_exprs).alias("properties"),
                    ]
                ).struct.json_encode()
                payloads = df_chunk.select(value_expr.alias("edge_value"))["edge_value"].to_arrow().cast("binary")
            else:
                payloads = []
                selected = [edge_id, source, target, edge_type, *property_columns]
                for row in df_chunk.select(selected).rows(named=True):
                    properties = {column: row[column] for column in property_columns if column != "type"}
                    properties["type"] = row[edge_type]
                    payloads.append(
                        entity_serializer.serialize(
                            Edge(
                                edge_id=row[edge_id],
                                source=row[source],
                                target=row[target],
                                properties=properties,
                            ),
                            "Edge",
                        )
                    )

            total += ingest_arrow_fn(
                df_chunk[edge_id].to_arrow(),
                df_chunk[source].to_arrow(),
                df_chunk[target].to_arrow(),
                df_chunk[edge_type].to_arrow(),
                payloads,
                append_only=append_only,
                native=native,
                chunk_size=chunk_size,
                index_mode=index_mode,
                progress=False,
            )
            if progress_bar is not None:
                progress_bar.update(df_chunk.height)
    return total


def ingest_edges_arrow_entities(store: KVStore, entity_serializer, raw_serializer,
                                indexed_edge_properties, stale_key: bytes,
                                adj_cache: dict, ingest_arrow_fn: Callable,
                                ingest_polars_entities_fn: Callable,
                                edge_ids, sources, targets, edge_types, *,
                                properties=None, append_only: bool = True,
                                native: bool = True, chunk_size: int = 100_000,
                                index_mode: str = "maintain",
                                progress: bool = False) -> int:
    """Ingest edge entity columns from Arrow-like columns."""
    try:
        import polars as pl
    except ImportError:
        pl = None

    if isinstance(raw_serializer, JSONSerializer) and pl is not None:
        data = {"edge_id": edge_ids, "source": sources, "target": targets, "edge_type": edge_types}
        for name, column in (properties or {}).items():
            data[name] = column
        return ingest_polars_entities_fn(
            pl.DataFrame(data),
            property_columns=list((properties or {}).keys()),
            append_only=append_only,
            native=native,
            chunk_size=chunk_size,
            index_mode=index_mode,
            progress=progress,
        )

    raw_edge_ids = edge_ids.to_pylist() if hasattr(edge_ids, "to_pylist") else list(edge_ids)
    raw_sources = sources.to_pylist() if hasattr(sources, "to_pylist") else list(sources)
    raw_targets = targets.to_pylist() if hasattr(targets, "to_pylist") else list(targets)
    raw_edge_types = edge_types.to_pylist() if hasattr(edge_types, "to_pylist") else list(edge_types)
    raw_properties = {
        name: column.to_pylist() if hasattr(column, "to_pylist") else list(column)
        for name, column in (properties or {}).items()
    }
    payloads = []
    for index, edge_key in enumerate(raw_edge_ids):
        edge_properties = {name: values[index] for name, values in raw_properties.items() if name != "type"}
        edge_properties["type"] = raw_edge_types[index]
        payloads.append(
            entity_serializer.serialize(
                Edge(
                    edge_id=edge_key,
                    source=raw_sources[index],
                    target=raw_targets[index],
                    properties=edge_properties,
                ),
                "Edge",
            )
        )
    return ingest_arrow_fn(
        raw_edge_ids,
        raw_sources,
        raw_targets,
        raw_edge_types,
        payloads,
        append_only=append_only,
        native=native,
        chunk_size=chunk_size,
        index_mode=index_mode,
        progress=progress,
    )
