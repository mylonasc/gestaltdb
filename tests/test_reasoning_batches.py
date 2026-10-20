"""Compact relation identity, set semantics, isolation and immutable ownership."""

from dataclasses import replace
import itertools
import random

import pytest

from gestaltdb.reasoning.batches import CompactRelation, RelationEvidence, RelationState, SymbolDictionary
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import RelationSchema


CONTEXT = InputContext("horizon", "world", "ontology", "policy", "mapping")
SCHEMA = RelationSchema("p", ("subject", "object"))


def dictionary():
    return SymbolDictionary.build(CONTEXT, ["a", "b", "c", "?x", "é", "urn:" + "x" * 1024])


def relation(rows=()):
    return CompactRelation.from_rows(SCHEMA, dictionary(), rows)


def test_dictionary_is_canonical_context_scoped_and_exact():
    first = dictionary()
    assert first == SymbolDictionary(CONTEXT, list(reversed(first.symbols)) + list(first.symbols))
    assert first.dictionary_id == SymbolDictionary(CONTEXT, first.symbols).dictionary_id
    for symbol in first.symbols:
        assert first.decode(first.encode(symbol)) == symbol
    with pytest.raises(KeyError):
        first.encode("unknown")
    with pytest.raises(TypeError):
        first._ids["new"] = 0
    for field in CONTEXT.__dataclass_fields__:
        other = replace(first, context=replace(CONTEXT, **{field: "different"}))
        assert other.dictionary_id != first.dictionary_id
        with pytest.raises(ReasoningError):
            first.require_compatible(other)
    assert SymbolDictionary(CONTEXT, ()).symbols == ()


@pytest.mark.parametrize("value", [-1, True, 1.5, None, 1 << 64, 1000])
def test_invalid_dictionary_ids(value):
    with pytest.raises(ReasoningError):
        dictionary().decode(value)
    with pytest.raises(ReasoningError):
        CompactRelation.from_encoded_rows(SCHEMA, dictionary(), [(value, 0)])


def test_immutable_columns_are_sorted_distinct_and_outlive_relation():
    rows = [("b", "a"), ("a", "b"), ("a", "b")]
    batch = relation(rows)
    assert list(batch.decoded_rows()) == sorted(set(rows))
    assert batch.nbytes == 32 and len(batch) == 2
    assert batch.digest == relation(reversed(rows)).digest
    view = batch.column(0)
    assert view.readonly and view.nbytes == 16
    with pytest.raises(TypeError):
        view[0] = 100
    del batch
    assert tuple(view) == (dictionary().encode("a"), dictionary().encode("b"))


def test_empty_nary_and_malformed_buffer_contracts():
    schema = RelationSchema("nary", ("a", "b", "c"))
    batch = CompactRelation.from_rows(schema, dictionary(), [("a", "b", "c")])
    assert batch.nbytes == 24 and list(batch.decoded_rows()) == [("a", "b", "c")]
    empty = CompactRelation.from_rows(schema, dictionary(), [])
    assert len(empty) == 0 and empty.nbytes == 0
    for buffers in [(b"",), (b"x", b"x", b"x"), (bytearray(),) * 3, (b"", b"12345678", b"")]:
        with pytest.raises(ReasoningError):
            CompactRelation(schema, dictionary(), buffers)
    for rows in [[(0,)], [(0, 0, 0)], ["ab"], [(None, 0)]]:
        with pytest.raises(ReasoningError):
            CompactRelation.from_encoded_rows(SCHEMA, dictionary(), rows)
    full = relation([("a", "b")])
    for index in (-1, True, "0", 2):
        with pytest.raises(ReasoningError):
            full.column(index)
    for buffers in [(full.buffers[0] * 2, full.buffers[1] * 2), ((1 << 64).to_bytes(9, "little"), b"")]:
        with pytest.raises(ReasoningError):
            CompactRelation(SCHEMA, dictionary(), buffers)


def test_limits_count_attempted_inputs_and_fail_without_mutating_previous_generation():
    seen = []

    def endless():
        for i in itertools.count():
            seen.append(i)
            yield (0, 0)

    with pytest.raises(ReasoningLimitError, match="max_input_rows"):
        CompactRelation.from_encoded_rows(SCHEMA, dictionary(), endless(), max_input_rows=2)
    assert seen == [0, 1, 2]
    with pytest.raises(ReasoningLimitError, match="max_input_symbols"):
        SymbolDictionary.build(CONTEXT, itertools.repeat("a"), max_input_symbols=2)
    with pytest.raises(ReasoningLimitError, match="max_symbols"):
        SymbolDictionary.build(CONTEXT, ["a", "b"], max_symbols=1)
    state = RelationState.seed(relation([("a", "a")]))
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        state.advance(relation([("b", "b")]), max_rows=1)
    assert list(state.stable.decoded_rows()) == [("a", "a")]
    for limit in (0, True, -1, 1 << 64):
        with pytest.raises(ReasoningError):
            CompactRelation.from_rows(SCHEMA, dictionary(), [], max_rows=limit)


def test_set_operations_and_delta_lifecycle_match_python_reference():
    rng = random.Random(20261021)
    domain = list(itertools.product("abc", repeat=2))
    state = RelationState.seed(relation())
    expected = set()
    for _ in range(30):
        rows = rng.choices(domain, k=10)
        batch = relation(rows)
        old = state
        state = state.advance(batch)
        assert set(state.delta.decoded_rows()) == set(rows) - expected
        expected.update(rows)
        assert set(state.stable.decoded_rows()) == expected
        assert old.stable.digest == relation(old.stable.decoded_rows()).digest
    assert not state.delta
    with pytest.raises(ReasoningError, match="subset"):
        RelationState(relation(), relation([("a", "a")]))


def test_relation_and_dictionary_mismatches_reject_numeric_id_collisions():
    first = relation([("a", "b")])
    other = replace(first, dictionary=replace(first.dictionary, context=replace(CONTEXT, world="other")))
    for operation in (first.union, first.difference, first.require_compatible):
        with pytest.raises(ReasoningError):
            operation(other)
        with pytest.raises(ReasoningError):
            operation(replace(first, schema=RelationSchema("q", SCHEMA.columns)))
    assert first.digest != other.digest


def test_exact_evidence_remains_separate_from_logical_multiplicity():
    batch = relation([("a", "b")] * 3)
    table = RelationEvidence(batch, [(0, "source-a:v1"), (0, "source-b:v1"), (0, "source-a:v1")])
    assert len(batch) == 1 and len(table.references) == 2
    assert table.relation.digest == batch.digest
    for references in [[(1, "missing")], [(True, "bad")], [(0, "")], [(0,)]]:
        with pytest.raises(ReasoningError):
            RelationEvidence(batch, references)


def test_core_runs_without_site_packages_or_storage_imports():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.batches import SymbolDictionary, CompactRelation, RelationState
from standalone.protocols import InputContext
from standalone.relational import RelationSchema
d = SymbolDictionary(InputContext('h','w','o','p','m'), ('a','b'))
r = CompactRelation.from_rows(RelationSchema('p', ('s','o')), d, [('a','b')])
assert len(RelationState.seed(r).advance(r).delta) == 0
assert not any(name.startswith(('gestaltdb', 'numpy', 'pyarrow', 'polars')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_documented_core_example_runs():
    from pathlib import Path
    import textwrap

    text = (Path(__file__).parents[1] / "docs/reasoning-batches.rst").read_text()
    block = text.split(".. code-block:: python\n\n", 1)[1].split("\n\n``CompactRelation``", 1)[0]
    exec(compile(textwrap.dedent(block), "reasoning-batches.rst", "exec"), {})


def test_batch_benchmark_reference_fixture():
    from benchmarks.reasoning_batches import worker

    result = worker(10, False, False)
    assert result["rows"] == 10 and result["column_payload_bytes"] == 160
    assert result["dictionary_symbols"] == 11
    assert result["execution_kind"] == "reference-build-and-set-operations-not-inference"
