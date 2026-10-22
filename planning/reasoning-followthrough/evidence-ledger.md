# Exact-version ledger transition contract — #250

Status: design milestone; adapter admission depends on #248. No string token
in this design independently authenticates a backend snapshot.

The ledger maps an exact immutable assertion/version ID to one logical typed
fact. It maintains the inverse set of active version IDs per fact. Derivations
remain in the maintenance support index, not this asserted-evidence ledger.

| Atomic transition | Logical maintenance delta |
|---|---|
| Assert first version for fact F | Insert F |
| Assert second source/version for F | No logical insertion; retain both IDs |
| Retract one of two versions for F | No logical retraction |
| Retract final version for F | Retract F |
| Retry identical assertion/retraction | Idempotent state and delta |
| Same version ID, different fact | Reject before state change |
| Correction old-version → new-version | Apply both atomically, then diff logical sets |

Adapter fixtures must exercise two agents/sources asserting the same proposition,
confidence-only correction, object-changing correction, retraction/resurrection,
and source-policy changes. Persist tombstones or rely on authenticated immutable
database versions before claiming cross-restart version-ID reuse protection.
Do not retract a derived fact merely because one asserted version disappears.
