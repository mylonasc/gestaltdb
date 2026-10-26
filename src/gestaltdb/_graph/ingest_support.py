"""Columnar-ingestion support: modes, progress, frames, chunk index upkeep.

Small, mostly pure helpers for Arrow/Polars bulk paths: index-mode
validation and coercion, the tqdm/`SimpleProgress` reporter, Polars frame
introspection and chunking, and per-chunk secondary-index maintenance for
opaque node payloads. The chunked write loops themselves move separately.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..ingestion import ColumnarIngestionMode, IndexMaintenanceMode, NodeList
from . import index_maintenance as _index_maintenance
from .entities import (
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
)
from .node_records import get_node

_VALID_INDEX_MODES = {IndexMaintenanceMode.MAINTAIN.value, IndexMaintenanceMode.DEFER.value}


class SimpleProgress:
    """Small stderr progress fallback used when tqdm is unavailable."""

    def __init__(self, *, total=None, desc: str = "ingest", unit: str = "rows"):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.current = 0
        self.started_at = time.perf_counter()

    def update(self, count: int) -> None:
        self.current += count
        elapsed = max(time.perf_counter() - self.started_at, 1e-9)
        rate = self.current / elapsed
        total = "?" if self.total is None else str(self.total)
        print(f"{self.desc}: {self.current}/{total} {self.unit} ({rate:,.0f} {self.unit}/s)", file=sys.stderr)

    def close(self) -> None:
        pass


def validate_index_mode(index_mode) -> None:
    """Validate a columnar ingestion index mode."""
    if isinstance(index_mode, IndexMaintenanceMode):
        index_mode = index_mode.value
    if index_mode not in _VALID_INDEX_MODES:
        allowed = ", ".join(sorted(_VALID_INDEX_MODES))
        raise ValueError(f"index_mode must be one of: {allowed}")


def low_level_index_mode(index_mode) -> str:
    """Return the low-level index mode used by primitive ingestion calls."""
    if isinstance(index_mode, IndexMaintenanceMode):
        index_mode = index_mode.value
    if index_mode == IndexMaintenanceMode.DEFER_REBUILD.value:
        return IndexMaintenanceMode.DEFER.value
    validate_index_mode(index_mode)
    index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    return index_mode


def coerce_columnar_ingestion_mode(ingestion_mode) -> str:
    """Normalize a high-level columnar ingestion mode."""
    if isinstance(ingestion_mode, ColumnarIngestionMode):
        return ingestion_mode.value
    values = {mode.value for mode in ColumnarIngestionMode}
    if ingestion_mode not in values:
        allowed = ", ".join(sorted(values))
        raise ValueError(f"ingestion_mode must be one of: {allowed}")
    return ingestion_mode


def should_rebuild_after_ingest(index_mode) -> bool:
    """Return whether high-level ingestion should rebuild deferred indexes."""
    return (index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode) == IndexMaintenanceMode.DEFER_REBUILD.value


@contextmanager
def ingestion_progress(enabled: bool, *, total=None, desc: str):
    """Return an optional progress reporter for chunked ingestion."""
    if not enabled:
        yield None
        return
    try:
        from tqdm.auto import tqdm
    except ImportError:
        progress = SimpleProgress(total=total, desc=desc)
    else:
        progress = tqdm(total=total, desc=desc, unit="rows", unit_scale=True)
    try:
        yield progress
    finally:
        progress.close()


def polars_labels_expr(pl, df, labels):
    """Return a Polars expression for node labels."""
    if labels is None:
        return pl.lit([])
    if isinstance(labels, str):
        return pl.col(labels) if labels in df.columns else pl.lit([])
    return pl.lit(list(labels))


def polars_properties_expr(pl, property_columns):
    """Return a Polars expression for an entity properties object."""
    if not property_columns:
        return pl.lit({})
    return pl.struct(list(property_columns))


def validate_polars_frame(pl, df, feature_name: str) -> None:
    """Validate that input is a Polars eager or lazy frame."""
    if not isinstance(df, (pl.DataFrame, pl.LazyFrame)):
        raise TypeError(f"df must be a polars.DataFrame or polars.LazyFrame for {feature_name}")


def polars_columns(pl, df) -> list[str]:
    """Return column names without collecting LazyFrame data."""
    if isinstance(df, pl.LazyFrame):
        return df.collect_schema().names()
    return df.columns


def polars_row_count(pl, df):
    """Return an eager row count, or None for lazy inputs."""
    if isinstance(df, pl.DataFrame):
        return df.height
    return None


def iter_polars_chunks(pl, df, chunk_size: int):
    """Yield eager DataFrame chunks from eager or lazy Polars input."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if isinstance(df, pl.DataFrame):
        for start in range(0, df.height, chunk_size):
            yield df.slice(start, chunk_size)
        return

    if hasattr(df, "collect_batches"):
        for chunk in df.collect_batches(chunk_size=chunk_size):
            if chunk.height:
                yield chunk
        return

    offset = 0
    while True:
        chunk = df.slice(offset, chunk_size).collect()
        if chunk.height == 0:
            break
        yield chunk
        if chunk.height < chunk_size:
            break
        offset += chunk_size


def delete_existing_node_indexes_for_columnar_chunk(store: KVStore, entity_serializer,
                                                     indexed_node_properties,
                                                     chunk: NodeList) -> None:
    """Remove stale indexes before opaque columnar node upserts."""
    for node_id in chunk.node_ids:
        old_node = get_node(store, entity_serializer, node_id)
        if old_node is not None:
            _index_maintenance.delete_node_indexes(store, indexed_node_properties, old_node)


def put_node_indexes_for_columnar_chunk(store: KVStore, entity_serializer,
                                        indexed_node_properties,
                                        chunk: NodeList) -> None:
    """Maintain label and configured property indexes for columnar nodes."""
    entries = []
    range_entries = []
    for node_value in chunk.node_values:
        node = entity_serializer.deserialize(node_value, "Node")
        for label in node.labels:
            entries.append(("node_label", [label.encode("utf-8")], node.get_id_bytes))
        for property_name in indexed_node_properties:
            if property_name in node.properties:
                raw_value = node.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append((
                    "node_property",
                    [property_name.encode("utf-8"), property_value],
                    node.get_id_bytes,
                ))
                for label in node.labels:
                    entries.append((
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                        node.get_id_bytes,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node.get_id_bytes))
                    for label in node.labels:
                        range_entries.append(("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node.get_id_bytes))
    if entries:
        store.put_index_entries_bulk(entries)
    if range_entries:
        store.put_range_index_entries_bulk(range_entries)
