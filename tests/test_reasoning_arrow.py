"""Optional Arrow exchange: version/dictionary binding, offsets and lifetimes."""

from dataclasses import replace
import gc
import sys

import pytest

from gestaltdb.reasoning.adapters.arrow import from_arrow, to_arrow
from gestaltdb.reasoning.batches import CompactRelation, SymbolDictionary
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import RelationSchema


CONTEXT = InputContext("h", "w", "o", "p", "m")
SCHEMA = RelationSchema("p", ("subject", "object"))


def relation():
    dictionary = SymbolDictionary(CONTEXT, ("a", "b", "c"))
    return CompactRelation.from_rows(SCHEMA, dictionary, [("a", "b"), ("b", "c"), ("c", "a")])


def test_missing_optional_dependency_is_clear_and_does_not_affect_core(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    batch = relation()
    assert len(batch) == 3
    with pytest.raises(ImportError, match=r"gestaltdb\[arrow\]"):
        to_arrow(batch)


def test_buffers_export_readonly_and_survive_relation_collection():
    pa = pytest.importorskip("pyarrow")
    batch = relation()
    table = to_arrow(batch)
    if sys.byteorder == "little":
        assert table.column(0).chunk(0).buffers()[1].address == pa.py_buffer(batch.buffers[0]).address
    assert not table.column(0).chunk(0).buffers()[1].is_mutable
    reader = table.to_reader(max_chunksize=1)
    schema, dictionary = batch.schema, batch.dictionary
    del batch, table
    gc.collect()
    imported = from_arrow(reader, schema=schema, dictionary=dictionary, batch_rows=1)
    reader.close()
    del reader
    gc.collect()
    assert list(imported.decoded_rows()) == [("a", "b"), ("b", "c"), ("c", "a")]


def test_chunked_offset_arrays_and_duplicate_rows_are_canonicalized():
    pa = pytest.importorskip("pyarrow")
    batch = relation()
    table = to_arrow(batch)
    sliced = pa.concat_tables([table.slice(1, 2), table.slice(0, 1), table.slice(1, 1)])
    assert from_arrow(sliced, schema=SCHEMA, dictionary=batch.dictionary, batch_rows=1) == batch
    record = table.to_batches()[0].slice(1, 1)
    assert list(from_arrow(record, schema=SCHEMA, dictionary=batch.dictionary).decoded_rows()) == [("b", "c")]
    empty = CompactRelation.from_rows(SCHEMA, batch.dictionary, [])
    assert from_arrow(to_arrow(empty), schema=SCHEMA, dictionary=batch.dictionary) == empty


def test_c_stream_capsules_and_polars_consumer_roundtrip():
    pa = pytest.importorskip("pyarrow")
    batch = relation()

    class Producer:
        def __arrow_c_stream__(self, requested_schema=None):
            return to_arrow(batch).__arrow_c_stream__(requested_schema)

    assert from_arrow(Producer(), schema=SCHEMA, dictionary=batch.dictionary) == batch
    pl = pytest.importorskip("polars")
    source = to_arrow(batch)
    result = pl.from_arrow(source).filter(pl.col("subject") != batch.dictionary.encode("a")).to_arrow()
    # Polars drops custom metadata. Restoring it is an explicit caller assertion
    # that no recoding/context change occurred; import still validates all IDs.
    result = result.replace_schema_metadata(source.schema.metadata)
    imported = from_arrow(result, schema=SCHEMA, dictionary=batch.dictionary)
    assert list(imported.decoded_rows()) == [("b", "c"), ("c", "a")]


def test_metadata_type_null_and_id_mismatches_fail_closed():
    pa = pytest.importorskip("pyarrow")
    batch = relation()
    table = to_arrow(batch)
    inputs = [table.replace_schema_metadata(None),
              table.replace_schema_metadata({**table.schema.metadata, b"gestaltdb.relation.format": b"future"}),
              table.rename_columns(["object", "subject"]),
              pa.table({"subject": pa.array([0], type=pa.int64()), "object": pa.array([1], type=pa.uint64())}).replace_schema_metadata(table.schema.metadata),
              pa.table({"subject": pa.array([None], type=pa.uint64()), "object": pa.array([0], type=pa.uint64())}).replace_schema_metadata(table.schema.metadata),
              pa.table({"subject": pa.array([1 << 63], type=pa.uint64()), "object": pa.array([0], type=pa.uint64())}).replace_schema_metadata(table.schema.metadata)]
    for invalid in inputs:
        with pytest.raises(ReasoningError):
            from_arrow(invalid, schema=SCHEMA, dictionary=batch.dictionary)
    with pytest.raises(ReasoningError, match="metadata"):
        from_arrow(table, schema=SCHEMA, dictionary=replace(batch.dictionary, context=replace(CONTEXT, world="other")))
    with pytest.raises(ReasoningLimitError, match="max_input_rows"):
        from_arrow(table, schema=SCHEMA, dictionary=batch.dictionary, max_input_rows=2)
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        from_arrow(table, schema=SCHEMA, dictionary=batch.dictionary, max_rows=2)
    with pytest.raises(ReasoningError):
        from_arrow(table, schema=SCHEMA, dictionary=batch.dictionary, batch_rows=0)


def test_stream_failure_is_not_a_successful_partial_relation():
    pa = pytest.importorskip("pyarrow")
    batch = relation()
    table = to_arrow(batch)

    def broken():
        yield table.to_batches()[0]
        raise RuntimeError("source failed")

    reader = pa.RecordBatchReader.from_batches(table.schema, broken())
    with pytest.raises(RuntimeError, match="source failed"):
        from_arrow(reader, schema=SCHEMA, dictionary=batch.dictionary)
    reader.close()


def test_stream_budget_is_cumulative_across_batches():
    pa = pytest.importorskip("pyarrow")
    batch = relation()
    table = to_arrow(batch)
    reader = table.to_reader(max_chunksize=1)
    with pytest.raises(ReasoningLimitError, match="max_input_rows"):
        from_arrow(reader, schema=SCHEMA, dictionary=batch.dictionary, max_input_rows=2)
    reader.close()


def test_documented_arrow_example_runs():
    pytest.importorskip("pyarrow")
    from pathlib import Path
    import textwrap

    text = (Path(__file__).parents[1] / "docs/reasoning-batches.rst").read_text()
    block = text.split(".. code-block:: python\n\n")[2].split("\n\nExports are", 1)[0]
    exec(compile(textwrap.dedent(block), "reasoning-batches.rst", "exec"), {})
