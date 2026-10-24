# Durable reasoning session state machine — #251

Status: executable advance-in-memory milestone implemented in
`reasoning/adapters/sessions.py` (see `docs/reasoning-sessions.rst`); durable
cross-process persistence of session state remains open. The generation (#248)
and evidence (#250) contracts it depends on are now merged.

## States

1. **Pinned**: authenticate source commit prefix, rule catalog, policy and profile.
2. **Computing**: consume exact version deltas into the evidence ledger; update
   logical closure and complete bounded support/dependency state off the writer lock.
3. **Staged**: persist immutable generation records plus completion metadata,
   retaining the previous visible generation. Incomplete records are not readable.
4. **Publishing**: under the shared writer guard, verify source freshness policy
   and target CAS, then atomically change the visible-generation identity.
5. **Visible**: queries/snapshots pin that generation; keep prior generations until
   reader-retention policy permits explicit garbage collection.

Source deltas include correction/retraction semantics, not merely newly present
facts. A missing catalog, unsupported snapshot, incomplete dependency index or
unknown delta history requires explicit rematerialization, never guessed repair.

## Fault and concurrency matrix

Inject interruption before/after every state boundary, duplicate delta delivery,
source change after staging, two publishers from the same target base, and restart
with a changed catalog/policy. Assert old-or-new visibility, no reused commit IDs,
no loss of alternative evidence, and equality with fresh recomputation. Capture
persisted support bytes and write amplification separately from inference time.

Initially preserve the legacy evaluator; no witness-only substitution is valid.
