:orphan:

Opt-in generation identity and admission
========================================

``gestaltdb.reasoning.generation.GenerationIdentity`` composes a temporal or current-snapshot source
identity, ``InputContext``, program/catalog digests, semantic profile and exact
dictionary identity. ``require_compatible`` rejects any logical mismatch before
reuse. ``require_compilation`` compares the logical plan fields only: it does
not select an executor or replace executor-specific plan admission.

These storage-neutral values are compatibility metadata, not authentication.
Use ``gestaltdb.reasoning.adapters.generation.bind_claim_generation`` to verify
a ``ClaimReasoningResult`` against its database and bind a content-compatible
``RuleCatalog``. ``catalog_for_ontology`` constructs a catalog from compiled
rule content without changing legacy program digests. The initial profile is
positive binary entity reasoning. Current-graph and temporal-range admission
are not implemented by this bridge; typed point sessions have a separate
explicit admission path below.

For distinct ``CurrentSourceIdentity`` values and authenticated complete mutable
projection through executor admission, see :doc:`reasoning-current-admission`.
Temporal and current identities are never interchangeable; existing temporal
digests are preserved byte-for-byte. The temporal bridges described here remain
temporal-only.

The resulting ``ClaimGenerationBinding.verify_source(graph)`` authenticates
the original prefix and conservatively rejects any newer commit. Explicit
``freshness="historical"`` verifies the old prefix instead. This read-only check
is not an atomic publication permit: publishers must hold their writer guard
through checking and committing. Staged binary point publication now carries
and revalidates this binding (see :doc:`reasoning-generations`); other publication
and cache paths are not migrated. No identity string or result record independently proves that an
inference computation is sound.

Typed point sessions
--------------------

``gestaltdb.reasoning.adapters.session_generation`` provides
``catalog_for_typed_program``, ``bind_session_generation``,
``SessionGenerationBinding`` and ``SessionAdmissionLimits``. The catalog
convention is exact rule-key coverage and a single-rule ``GenericProgram``
content digest per rule. External version IDs and ordinals are allowed and
remain part of the catalog identity; wrong or extra content fails admission.
Existing sessions are not silently rewritten.

Admission reads the session's exact historical ``through_commit`` prefix under
one temporal view. It independently compares **every active assertion version**,
mapped typed fact and logical claim ID to the session ledger. Two independent
assertions for one fact must both be present; equal logical fact sets alone
are insufficient. World, agent/source/derived policy, mapping, valid point,
program and catalog are bound to the shared identity. ``InputContext`` uses
the typed program digest as its revision (not an invented ontology revision).
The profile is ``positive-typed-point-v1`` and representation is
``typed-values-v1`` (external entity strings and typed literal values, not
compact dictionary IDs). Negation, aggregates and closed scopes fail closed.

``binding.verify(graph, session)`` rechecks the exact state digest, internal
ledger/index consistency and active source evidence. State digests match the
durable session format, so a loaded unchanged session can reuse its binding.
An advance, policy change, catalog version change or other state drift needs
explicit re-admission. Default ``freshness="latest"`` rejects any newer commit;
``freshness="historical"`` explicitly admits the old prefix, never current
state under an old horizon. No writes or automatic rebuilds occur during
admission. A caller that will publish must still hold the writer guard through
its final source check and commit.

``SessionAdmissionLimits`` bounds stored versions, facts, supports, commit
prefix length and yielded source candidates before policy filtering.
Backend-internal scans, envelope sizes and native calls are not memory/work
limits. ``cancel`` is cooperative between calls; native kernels are not
interrupted. Authentication and state encoding can themselves be expensive;
this is an explicit opt-in admission scan, not a fast cache lookup.

The bridge authenticates active source input and pins the supplied state; it
does **not** prove that closure or supports were computed correctly, nor audit
retired ledger history. It does not admit mutable graph projections, temporal
ranges, compact dictionaries or execution on arbitrary engines. Do not replace
those missing contracts with a point token. Source-independent specialized
program caches remain keyed by program/goal; they do not cache closure facts
and need no source-horizon invalidation.
