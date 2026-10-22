:orphan:

Opt-in generation identity and admission
========================================

``gestaltdb.reasoning.generation.GenerationIdentity`` composes a temporal source
identity, ``InputContext``, program/catalog digests, semantic profile and exact
dictionary identity. ``require_compatible`` rejects any logical mismatch before
reuse. ``require_compilation`` compares the logical plan fields only: it does
not select an executor or replace executor-specific plan admission.

These storage-neutral values are compatibility metadata, not authentication.
Use ``gestaltdb.reasoning.adapters.generation.bind_claim_generation`` to verify
a ``ClaimReasoningResult`` against its database and bind a content-compatible
``RuleCatalog``. ``catalog_for_ontology`` constructs a catalog from compiled
rule content without changing legacy program digests. The initial profile is
positive binary entity reasoning; typed, current-graph and temporal-range
admission are not implemented by this bridge.

The resulting ``ClaimGenerationBinding.verify_source(graph)`` authenticates
the original prefix and conservatively rejects any newer commit. Explicit
``freshness="historical"`` verifies the old prefix instead. This read-only check
is not an atomic publication permit: publishers must hold their writer guard
through checking and committing. Existing publication/cache APIs are not yet
migrated. No identity string or result record independently proves that an
inference computation is sound.
