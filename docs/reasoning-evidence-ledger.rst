:orphan:

Exact-version assertion ledger prototype
========================================

Import ``AssertionVersion`` and ``EvidenceLedger`` from
``gestaltdb.reasoning.evidence_ledger``. Each version ID binds one immutable
``TypedFact``. Multiple active versions may assert the same logical fact;
retiring one only removes that fact when its final active version disappears.
Derived supports belong to maintenance, not this asserted-evidence ledger.

``ledger.transition(scope_id=..., assertions=..., retractions=...)`` returns a
new ledger plus ``inserted`` and ``retracted`` logical fact tuples. Supply both
parts of a correction in one call: correcting a version to the same logical
fact produces no logical removal. Identical retries are idempotent. An old
assertion retry cannot reactivate a retired ID; resurrection needs a new ID.
Unknown retractions and conflicting version-ID reuse fail without changing the
original immutable ledger.

``max_versions`` bounds retained history, including tombstones, and each input
stream. Reaching the bound requires explicit reconciliation or durable external
history; the ledger never silently forgets identity. It is an in-memory
prototype, not a durable database session or delta reader. ``scope_id`` must
represent a stable database/world/selection/mapping scope, not a changing commit
horizon. Adapters must authenticate source horizons and commit transitions
before calling this API; caller-supplied scope strings do not authenticate data.
