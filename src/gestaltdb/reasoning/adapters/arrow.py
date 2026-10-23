"""Optional strict Arrow exchange for compact relation batches, not raw KV scans.

Exports share immutable column payloads on little-endian machines. Imports copy,
validate and canonicalize; neither decoding storage values nor native reasoning
execution is implied. PyArrow is loaded only when an exchange function is called.
"""

from __future__ import annotations

from array import array
import sys

from ..batches import CompactRelation, SymbolDictionary, _positive
from ..model import ReasoningError, ReasoningLimitError
from ..relational import RelationSchema


def _pyarrow():
    try:
        import pyarrow
    except ImportError as exc:
        raise ImportError("compact relation exchange requires gestaltdb[arrow]") from exc
    return pyarrow


def _metadata(schema, dictionary):
    return {b"gestaltdb.relation.format": b"compact-uint64-v1",
            b"gestaltdb.relation.name": schema.name.encode("utf-8"),
            b"gestaltdb.dictionary.id": dictionary.dictionary_id.encode("ascii")}


def to_arrow(relation: CompactRelation):
    """Return a Table owning references to immutable uint64 payloads.

    PyArrow provides the Arrow C Stream protocol for consumers such as Polars.
    Views/batches retain buffer owners after the original relation is collected.
    On big-endian platforms, byte-swapped copies are required by Arrow's format.
    """
    if not isinstance(relation, CompactRelation):
        raise ReasoningError("relation must be a CompactRelation")
    pa = _pyarrow()
    columns = []
    for buffer in relation.buffers:
        if sys.byteorder != "little":
            values = array("Q")
            values.frombytes(buffer)
            values.byteswap()
            buffer = values.tobytes()
        columns.append(pa.Array.from_buffers(pa.uint64(), len(relation), [None, pa.py_buffer(buffer)]))
    schema = pa.schema([pa.field(name, pa.uint64(), nullable=False) for name in relation.schema.columns],
                       metadata=_metadata(relation.schema, relation.dictionary))
    return pa.Table.from_arrays(columns, schema=schema)


def from_arrow(source, *, schema: RelationSchema, dictionary: SymbolDictionary,
               max_input_rows: int = 100_000, max_rows: int = 100_000,
               batch_rows: int = 65_536) -> CompactRelation:
    """Import a Table, RecordBatch, reader or Arrow C Stream producer.

    Requires version/name/dictionary metadata and ordered uint64 columns, with no
    nulls. Chunk and array offsets are honored. Data is copied and set-normalized;
    duplicate input rows count against input bounds. A borrowed reader stays
    caller-owned; a reader created from a C Stream is closed on all exit paths.
    External producers must themselves obey their memory/snapshot contracts.
    """
    if not isinstance(schema, RelationSchema) or not isinstance(dictionary, SymbolDictionary):
        raise ReasoningError("RelationSchema and SymbolDictionary required")
    _positive(max_input_rows, "max_input_rows")
    _positive(max_rows, "max_rows")
    _positive(batch_rows, "batch_rows")
    pa = _pyarrow()
    owned_reader = None
    if isinstance(source, pa.RecordBatch):
        batches, source_schema = [source], source.schema
    elif isinstance(source, pa.Table):
        if source.num_rows > max_input_rows:
            raise ReasoningLimitError("max_input_rows exceeded")
        batches, source_schema = source.to_batches(max_chunksize=batch_rows), source.schema
    elif isinstance(source, pa.RecordBatchReader):
        batches, source_schema = source, source.schema
    elif hasattr(source, "__arrow_c_stream__"):
        owned_reader = pa.RecordBatchReader.from_stream(source)
        batches, source_schema = owned_reader, owned_reader.schema
    else:
        raise ReasoningError("Arrow Table, RecordBatch, reader or C Stream producer required")

    def validate_schema(actual):
        metadata = actual.metadata or {}
        if any(metadata.get(key) != value for key, value in _metadata(schema, dictionary).items()):
            raise ReasoningError("incompatible Arrow version, relation or dictionary metadata")
        if actual.names != list(schema.columns) or any(field.type != pa.uint64() for field in actual):
            raise ReasoningError("ordered uint64 entity columns required")

    def encoded_rows():
        validate_schema(source_schema)
        count = 0
        for batch in batches:
            validate_schema(batch.schema)
            count += batch.num_rows
            if count > max_input_rows:
                raise ReasoningLimitError("max_input_rows exceeded")
            for start in range(0, batch.num_rows, batch_rows):
                part = batch.slice(start, batch_rows)
                columns = []
                for column in part.columns:
                    if column.null_count:
                        raise ReasoningError("null entity IDs are not supported")
                    buffer = column.buffers()[1]
                    if buffer is None:
                        raise ReasoningError("missing uint64 value buffer")
                    raw = buffer.slice(column.offset * 8, len(column) * 8).to_pybytes()
                    if sys.byteorder != "little":
                        values = array("Q")
                        values.frombytes(raw)
                        values.byteswap()
                        raw = values.tobytes()
                    columns.append(memoryview(raw).cast("Q"))
                yield from zip(*columns)

    try:
        return CompactRelation.from_encoded_rows(schema, dictionary, encoded_rows(),
                                                 max_input_rows=max_input_rows, max_rows=max_rows)
    finally:
        if owned_reader is not None:
            owned_reader.close()
