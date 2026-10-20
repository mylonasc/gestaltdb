:orphan:

Compact relation batches and Arrow exchange
============================================================

``gestaltdb.reasoning.batches`` supplies a standard-library-only reference batch
boundary for the relational IR. It adds no execution semantics to
``DatalogEngine``. It does not change database IDs, durable keys or serializers,
decode stored KV payloads, or implement native joins.

Dictionaries and immutable generations
----------------------------------------

``SymbolDictionary`` sorts distinct nonempty named symbols, assigns zero-based
uint64 IDs and binds its generation identity to the complete existing
``InputContext``. Input order and duplicates do not affect identity; any context
field or symbol change does. New symbols require a new dictionary; never append
to an existing generation or reuse integers without the dictionary identity.
These are entity/relation symbols, not literal-value or OWL equality classes.

.. code-block:: python

   from gestaltdb.reasoning.batches import (
       CompactRelation, RelationEvidence, RelationState, SymbolDictionary,
   )
   from gestaltdb.reasoning.protocols import InputContext
   from gestaltdb.reasoning.relational import RelationSchema

   context = InputContext("verified-horizon", "default", "ontology-v1", "policy-v1", "mapping-v1")
   dictionary = SymbolDictionary.build(context, ["alice", "bob", "carol"])
   schema = RelationSchema("parent", ("subject", "object"))
   base = CompactRelation.from_rows(schema, dictionary, [("alice", "bob")] * 2)
   assert len(base) == 1 and base.nbytes == 16
   evidence = RelationEvidence(base, [(0, "claim-version-a"), (0, "claim-version-b")])
   assert len(evidence.references) == 2
   candidates = CompactRelation.from_rows(schema, dictionary, [("bob", "carol")])
   state = RelationState.seed(base).advance(candidates)
   assert list(state.delta.decoded_rows()) == [("bob", "carol")]
   assert len(state.stable) == 2
   assert len(state.advance(candidates).delta) == 0

``CompactRelation`` holds one immutable, eight-byte-per-ID column buffer per
schema column. Rows are lexicographically sorted and deduplicated by IDs. Positive
arities are supported as containers, not as a new evaluator API. ``column(i)``
returns a read-only memoryview retaining its bytes owner after relation disposal.
Native byte order is internal only, not a durable serialization format; digest
identity is independent of byte order. Empty relations retain their full schema.

``from_rows`` encodes named symbols; unknown symbols raise ``KeyError``.
``from_encoded_rows`` rejects bool, negative, out-of-range, floating or unknown
IDs. Builders bound attempted inputs (including duplicates) and distinct output
rows, stopping after at most one extra input to detect overflow. Source iterator
errors propagate and no partially constructed relation is returned. Existing
immutable states remain unchanged after failure. Bounds are counters, not exact
wall-clock, allocated-capacity or process-RSS guarantees.

``union`` and ``difference`` require identical schema and dictionary generations.
``RelationState.advance`` subtracts stable facts from candidates to form the new
delta and returns a new accumulated stable generation. No source scans or database
publication occur. Evidence remains a separately bound relation/row-index side
table; set operations do not implicitly propagate justifications or reindex these
references. This is not all-support provenance or deletion maintenance.

The reference builders and set operations use transient Python tuples/sets and
copy into compact buffers. ``nbytes`` counts final column payloads only, excluding
dictionaries, reverse maps and staging allocations. The dictionary retains full
external strings and a Python reverse index. Compact output does not imply compact
peak RSS, compressed storage or vectorized execution. Optimized joins are later
executor work, not a hidden fallback claim.

Optional Arrow boundary
-----------------------

Install ``gestaltdb[arrow]`` (or ``gestaltdb[polars]``) for exchange functions in
``gestaltdb.reasoning.adapters.arrow``. Importing the module alone loads neither
PyArrow nor Polars; missing PyArrow fails with the required extra at operation time.

.. code-block:: python

   from gestaltdb.reasoning.adapters.arrow import from_arrow, to_arrow
   from gestaltdb.reasoning.batches import CompactRelation, SymbolDictionary
   from gestaltdb.reasoning.protocols import InputContext
   from gestaltdb.reasoning.relational import RelationSchema

   dictionary = SymbolDictionary(InputContext("h", "w", "o", "p", "m"), ("a", "b"))
   schema = RelationSchema("p", ("subject", "object"))
   relation = CompactRelation.from_rows(schema, dictionary, [("a", "b")])
   table = to_arrow(relation)
   with table.to_reader(max_chunksize=1024) as reader:
       restored = from_arrow(reader, schema=schema, dictionary=dictionary)
   assert restored == relation

Exports are non-null uint64 fields in schema-column order. Required schema
metadata identifies ``compact-uint64-v1``, relation name and complete dictionary
generation. PyArrow Tables provide Arrow C Stream interoperability; consumers
such as Polars can operate on the integer columns. Exported arrays share immutable
payloads on little-endian platforms; big-endian export requires byte-swapped
copies. Array/schema wrappers still allocate; downstream consumers may copy.
Array owners remain valid after relation/stream disposal.

Imports accept Tables, RecordBatches, readers and C Stream producers. They require
matching metadata, exact ordered uint64 column types and no actual null IDs.
Nullable field declarations are accepted if values contain no nulls (for consumer
compatibility); negative/signed IDs and implicit casts are not accepted. Chunk
and array offsets are honored. Imports copy, validate, sort and deduplicate under
``max_input_rows``/``max_rows`` counters; ``batch_rows`` bounds per-chunk copying,
not consumer-produced batches or total staging memory. Duplicate inputs consume
input budget even when output is smaller. A caller-owned reader is not closed;
internally created C Stream readers close on success or failure.

Polars may drop custom Arrow metadata. After an operation preserving the dictionary
and schema, callers may explicitly restore the original metadata using
``result.replace_schema_metadata(original.schema.metadata)`` before importing.
That is a caller assertion of unchanged ID meaning, **not source authentication**;
do not relabel a differently encoded table to bypass compatibility checks. Actual
IDs/types/nulls are still validated. A general Polars execution/provenance adapter
is RE-03 and must control this binding itself.

This is decoded logical-relation exchange, not the proposed PyRex raw-KV Arrow
scan API. PyRex issue #31 remains optional upstream extraction work. No snapshot,
transaction, storage ownership or zero-copy KV decoding guarantee is inferred
from these relation buffers.
