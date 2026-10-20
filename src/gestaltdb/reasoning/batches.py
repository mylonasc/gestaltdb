"""Immutable compact entity relations and snapshot-local dictionaries.

This is the standard-library reference batch boundary, not a vectorized join
engine. Builders/set operations intentionally use bounded Python rows. Optional
Arrow exchange lives in ``adapters.arrow``; no storage or array runtime is loaded.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from .model import ReasoningError, ReasoningLimitError, _text
from .protocols import InputContext
from .relational import RelationSchema, _digest, _sequence


UINT64_MAX = (1 << 64) - 1


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= UINT64_MAX:
        raise ReasoningError(f"{name} must be a positive uint64 integer")
    return value


def _bounded(values: Iterable, limit: int, name: str):
    _positive(limit, name)
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ReasoningError(f"{name} input must be iterable") from exc
    for index, value in enumerate(iterator):
        if index >= limit:
            raise ReasoningLimitError(f"{name} exceeded")
        yield value


@dataclass(frozen=True)
class SymbolDictionary:
    """Canonical zero-based uint64 IDs; meaningful only with ``dictionary_id``.

    All symbols are named entities/relations, not literal values. Context is an
    adapter-verified opaque identity, not proof of source authenticity. New
    symbols require a new dictionary and explicit recoding; IDs never extend in
    place. Persistent database IDs and key encodings are untouched.
    """

    context: InputContext
    symbols: tuple[str, ...]
    _ids: Mapping[str, int] = field(init=False, repr=False, compare=False, hash=False)
    _dictionary_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.context, InputContext):
            raise ReasoningError("context must be an InputContext")
        symbols = _sequence(self.symbols, "symbols")
        for symbol in symbols:
            _text(symbol, "symbol")
        symbols = tuple(sorted(set(symbols)))
        if len(symbols) > UINT64_MAX:
            raise ReasoningError("dictionary uint64 capacity exceeded")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "_ids", MappingProxyType({symbol: i for i, symbol in enumerate(symbols)}))
        context = self.context
        object.__setattr__(self, "_dictionary_id", _digest(["gestaltdb-symbol-dictionary-v1",
                           [context.context_id, context.world, context.ontology_revision,
                            context.source_policy_id, context.mapping_id], list(symbols)]))

    @classmethod
    def build(cls, context: InputContext, symbols: Iterable[str], *, max_input_symbols: int = 100_000,
              max_symbols: int = 100_000) -> SymbolDictionary:
        _positive(max_symbols, "max_symbols")
        unique = set()
        for symbol in _bounded(symbols, max_input_symbols, "max_input_symbols"):
            unique.add(_text(symbol, "symbol"))
            if len(unique) > max_symbols:
                raise ReasoningLimitError("max_symbols exceeded")
        return cls(context, tuple(unique))

    @property
    def dictionary_id(self) -> str:
        return self._dictionary_id

    def encode(self, symbol: str) -> int:
        return self._ids[_text(symbol, "symbol")]

    def decode(self, symbol_id: int) -> str:
        if (isinstance(symbol_id, bool) or not isinstance(symbol_id, int)
                or not 0 <= symbol_id < len(self.symbols)):
            raise ReasoningError("symbol ID outside this dictionary")
        return self.symbols[symbol_id]

    def require_compatible(self, other: SymbolDictionary) -> None:
        if not isinstance(other, SymbolDictionary) or self != other:
            raise ReasoningError("incompatible dictionaries; explicitly recode rows")


@dataclass(frozen=True)
class CompactRelation:
    """Canonical distinct encoded rows, stored in immutable uint64 columns.

    Buffers use native endianness and are not a durable serialization format.
    ``column`` returns a read-only view whose bytes owner outlives this object.
    Logical row order is lexicographic by compact IDs, not by evidence arrival.
    """

    schema: RelationSchema
    dictionary: SymbolDictionary
    buffers: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schema, RelationSchema) or not isinstance(self.dictionary, SymbolDictionary):
            raise ReasoningError("RelationSchema and SymbolDictionary required")
        buffers = _sequence(self.buffers, "buffers")
        if len(buffers) != len(self.schema.columns) or not all(type(b) is bytes for b in buffers):
            raise ReasoningError("one immutable bytes buffer required per column")
        if array("Q").itemsize != 8:
            raise ReasoningError("platform requires eight-byte unsigned long long")
        if len({len(b) for b in buffers}) != 1 or len(buffers[0]) % 8:
            raise ReasoningError("equal uint64-aligned column lengths required")
        object.__setattr__(self, "buffers", buffers)
        previous = None
        size = len(self.dictionary.symbols)
        for row in self.rows():
            if any(value >= size for value in row):
                raise ReasoningError("row contains unknown dictionary ID")
            if previous is not None and previous >= row:
                raise ReasoningError("encoded rows must be sorted and distinct")
            previous = row

    @classmethod
    def from_encoded_rows(cls, schema: RelationSchema, dictionary: SymbolDictionary,
                          rows: Iterable[tuple[int, ...]], *, max_input_rows: int = 100_000,
                          max_rows: int = 100_000) -> CompactRelation:
        if not isinstance(schema, RelationSchema) or not isinstance(dictionary, SymbolDictionary):
            raise ReasoningError("RelationSchema and SymbolDictionary required")
        _positive(max_rows, "max_rows")
        unique = set()
        for value in _bounded(rows, max_input_rows, "max_input_rows"):
            row = _sequence(value, "row")
            if len(row) != len(schema.columns):
                raise ReasoningError("row arity mismatch")
            for symbol_id in row:
                if (isinstance(symbol_id, bool) or not isinstance(symbol_id, int)
                        or not 0 <= symbol_id < len(dictionary.symbols)):
                    raise ReasoningError("row contains unknown dictionary ID")
            unique.add(row)
            if len(unique) > max_rows:
                raise ReasoningLimitError("max_rows exceeded")
        columns = [array("Q") for _ in schema.columns]
        for row in sorted(unique):
            for column, value in zip(columns, row):
                column.append(value)
        return cls(schema, dictionary, tuple(column.tobytes() for column in columns))

    @classmethod
    def from_rows(cls, schema: RelationSchema, dictionary: SymbolDictionary,
                  rows: Iterable[tuple[str, ...]], **limits) -> CompactRelation:
        if not isinstance(schema, RelationSchema) or not isinstance(dictionary, SymbolDictionary):
            raise ReasoningError("RelationSchema and SymbolDictionary required")
        try:
            source = iter(rows)
        except TypeError as exc:
            raise ReasoningError("rows must be iterable") from exc

        def encoded():
            for value in source:
                row = _sequence(value, "row")
                if len(row) != len(schema.columns):
                    raise ReasoningError("row arity mismatch")
                yield tuple(dictionary.encode(symbol) for symbol in row)

        return cls.from_encoded_rows(schema, dictionary, encoded(), **limits)

    def __len__(self) -> int:
        return len(self.buffers[0]) // 8

    @property
    def nbytes(self) -> int:
        """Column payload only; excludes dictionary, indexes and Python overhead."""
        return sum(len(buffer) for buffer in self.buffers)

    def column(self, index: int) -> memoryview:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.buffers):
            raise ReasoningError("column index out of bounds")
        return memoryview(self.buffers[index]).cast("Q")

    def rows(self):
        return zip(*(self.column(index) for index in range(len(self.buffers))))

    def decoded_rows(self):
        return (tuple(self.dictionary.decode(value) for value in row) for row in self.rows())

    @property
    def digest(self) -> str:
        # JSON integer encoding makes this identity independent of native byte order.
        return _digest(["gestaltdb-compact-relation-v1", self.dictionary.dictionary_id,
                        self.schema.name, list(self.schema.columns), list(self.rows())])

    def require_compatible(self, other: CompactRelation) -> None:
        if not isinstance(other, CompactRelation) or self.schema != other.schema:
            raise ReasoningError("incompatible relation schemas")
        self.dictionary.require_compatible(other.dictionary)

    def union(self, other: CompactRelation, *, max_rows: int = 100_000) -> CompactRelation:
        self.require_compatible(other)
        from itertools import chain

        return self.from_encoded_rows(self.schema, self.dictionary, chain(self.rows(), other.rows()),
                                      max_input_rows=max(1, len(self) + len(other)), max_rows=max_rows)

    def difference(self, other: CompactRelation, *, max_rows: int = 100_000) -> CompactRelation:
        self.require_compatible(other)
        excluded = set(other.rows())
        return self.from_encoded_rows(self.schema, self.dictionary,
                                      (row for row in self.rows() if row not in excluded),
                                      max_input_rows=max(1, len(self)), max_rows=max_rows)


@dataclass(frozen=True)
class RelationState:
    """Per-run immutable accumulated facts and the most recently added delta."""

    stable: CompactRelation
    delta: CompactRelation

    def __post_init__(self) -> None:
        if not isinstance(self.stable, CompactRelation):
            raise ReasoningError("stable must be a CompactRelation")
        self.stable.require_compatible(self.delta)
        if not set(self.delta.rows()) <= set(self.stable.rows()):
            raise ReasoningError("delta must be a subset of accumulated stable facts")

    @classmethod
    def seed(cls, relation: CompactRelation) -> RelationState:
        return cls(relation, relation)

    def advance(self, candidates: CompactRelation, *, max_rows: int = 100_000) -> RelationState:
        added = candidates.difference(self.stable, max_rows=max_rows)
        return RelationState(self.stable.union(added, max_rows=max_rows), added)


@dataclass(frozen=True)
class RelationEvidence:
    """Exact evidence references separate from logical row multiplicity.

    Row indexes belong to precisely ``relation.digest``. Set operations do not
    implicitly carry/rewrite supports: this is not a maintenance-provenance API.
    """

    relation: CompactRelation
    references: tuple[tuple[int, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.relation, CompactRelation):
            raise ReasoningError("relation must be a CompactRelation")
        references = _sequence(self.references, "references")
        validated = set()
        for value in references:
            reference = _sequence(value, "reference")
            if len(reference) != 2:
                raise ReasoningError("reference must contain a row index and exact ID")
            row, reference_id = reference
            if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row < len(self.relation):
                raise ReasoningError("evidence row outside relation")
            _text(reference_id, "reference ID")
            validated.add((row, reference_id))
        object.__setattr__(self, "references", tuple(sorted(validated)))
