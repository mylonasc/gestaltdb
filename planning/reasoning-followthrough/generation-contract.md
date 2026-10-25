# Generation admission contract — #248

Status: original-acceptance reconciliation at integration `6b51545` (#361).
#244, #245 and #246 are closed. Target: `feature/onto-dev-epic-2` only.
The shared contract and existing consumer paths below are implemented. GitHub
#248 closure still requires this reconciliation and final post-merge gates;
this is not closure of roadmap #156 or a universal legacy-API guarantee.

## Identity and authority

Keep `InputContext`, catalog pins and backend provenance as separate values;
compose them rather than treating a caller-provided digest as authentication.
A reusable generation must bind database identity, authenticated source horizon,
world, effective selection/mapping, program and catalog revisions, semantic
profile, and (when used) dictionary generation. Physical batch sizes are not
logical identity. Source versions and logical fact IDs are different keys.

## Admission decisions

| Change | Required action |
|---|---|
| Program/catalog content | Rebuild; never reuse segment derivations |
| Effective selection, world or mapping | New generation and fresh extraction |
| Source horizon | Latest mode retries; historical mode verifies its pinned prefix |
| Dictionary generation | Explicit recoding or recompilation |
| Batch size | Reuse logical identity; replan physical execution if necessary |
| Unsupported backend snapshot | Fail before inference/publication |

Implemented in `reasoning/generation.py`, `reasoning/adapters/generation.py`
and `reasoning/adapters/session_generation.py`; see
`docs/reasoning-generation-identity.rst`. Binary point stages carry and verify
the binding, with optional exact caller pins. Typed sessions authenticate their
active assertion ledger against the exact historical source before admission.
Legacy digest values and persisted session formats remain unchanged.

The database adapter authenticates provenance. The stdlib core verifies exact
value compatibility only; it must not import GraphDB or acquire storage handles.

## First integration fixtures

Stage, correct a source claim, then publish: latest rejects without writes;
historical publication is opt-in and records the old horizon. Change a rule
without changing its ID: temporal reuse must rebuild. Repeat after target
movement and after dictionary rebinding. Verify all rejection paths preserve the
previous visible generation. The three prerequisite fixes and binary point
publication fixtures are merged. Typed session fixtures additionally compare
exact independent assertion versions, catalog content, scope/mapping, durable
state pins, and explicit bounds/cancellation across supported backends.

## Original acceptance reconciliation

The original acceptance is a shared admission contract (not interchangeable
opaque hashes), explicit historical/latest separation, inward-only core
boundaries, and mismatch fixtures before persistent consumer integration.
It is not a requirement to merge all source representations or invent a cache.

| Acceptance | Delivered contract | Executable evidence |
|---|---|---|
| Database and authenticated source horizon | Distinct `TemporalSourceIdentity`, `CurrentSourceIdentity`, `TemporalRangeSourceIdentity`; exact compatibility preserves existing point digests and never substitutes mutable sequence for temporal commit | `tests/test_reasoning_generation_identity.py`, `tests/test_reasoning_graph_execution.py`, `tests/test_reasoning_range_generation.py` |
| Program/catalog revision | Exact content-compatible catalog binding plus program/catalog digests; changed content requires rebuild/re-extraction | `tests/test_reasoning_generation_binding.py`, `tests/test_reasoning_session_generation.py` |
| World, selection and mapping | `InputContext` and explicit adapter policies, with active-version completeness rather than logical-fact-set equality alone | `tests/test_reasoning_session_generation.py`, `tests/test_reasoning_authenticated_inferred_sampler.py` |
| Semantic profile and dictionary/representation | Exact logical identity, distinct typed/external/compact representations; execution capability admission remains a separate decision | `tests/test_reasoning_admission.py`, `tests/test_reasoning_generation_identity.py` |
| Historical versus latest | Explicit historical prefix admission; latest rejects source movement; final publisher checks stay under writer guard and retain target CAS | `tests/test_reasoning_publication_freshness.py`, `tests/test_reasoning_bound_range_publication.py`, `tests/test_reasoning_authenticated_sessions.py` |
| Inward-only core | Compatibility values and pure evaluators do not acquire GraphDB/storage/authentication responsibilities; adapters authenticate sources | `tests/test_reasoning_contracts.py`, existing standalone installed-wheel and ontology conformance gates |
| Reject before persistent writes | Exact pin mismatches, source/decoder drift, bounds and cancellation preserve existing session/artifact bytes; staging/publication failures preserve prior visible state | `tests/test_reasoning_authenticated_sessions.py`, `tests/test_reasoning_authenticated_inferred_sampler.py`, `tests/test_reasoning_bound_range_publication.py` |

### Existing consumer inventory

| Consumer | Implementation / delivery | Admission and limits |
|---|---|---|
| Binary temporal claim extraction/result binding | `adapters/generation.py`, #261 | Source-verified binding and content-compatible catalog; compatibility is not closure proof |
| Binary point staged publication | `adapters/publication.py`, #325/#326 | Shared binding, optional exact caller generation pin, guarded source freshness and existing target comparison; no new blanket proof/durability claim |
| Typed point session binding | `adapters/session_generation.py`, #329 | Independent complete active-version/typed-fact/logical-ID parity, bounded source scan and exact session-state pin |
| Complete current-graph execution | `adapters/graph_execution.py`, #353 / child #352 | Distinct snapshot identity, complete authenticated projection and explicit executor dispatch; not persistent mutable-snapshot reopening or frontier-result admission |
| Finite-range computation/re-admission | `adapters/range_generation.py`, #358 / child #354 | Full historical-boundary extraction, exact version intervals; re-admission re-extracts and compares fresh closure/derivations before conformance |
| Authenticated range publication | `adapters/range_publication.py`, #359 / child #355 | Computation-owned stage, guarded re-admission/freshness, target history CAS and one interval-batch commit; legacy caller-derived `stage_range` stays separate |
| Durable v1 session load/advance | `adapters/authenticated_sessions.py`, #360 / child #356 | Exact caller generation/state pins; authenticates old input and latest new output before exact-state CAS, with cooperating process locks |
| Existing inferred sampler build/hydration | `adapters/authenticated_inferred_sampler.py`, #361 / child #357 | Exact caller generation/state pins, separate canonical projection selection, full versioned metadata; complete independent artifact reconstruction, not stored-checksum trust |

Authenticated durable and inferred APIs are explicit additive paths. Existing
unverified session/inferred builders, raw sampler loaders and low-level catalog
pins are not silently relabelled as independent source admission. No on-disk
session format or old identity digest is silently migrated. No new sampling
algorithm or automatic publication/execution path is introduced.

### Cache and pin classification

- `demand.SpecializationCache` stores specialized **programs**, keyed by program
  digest and exact goal. It stores no source facts/horizon; source invalidation
  would be irrelevant and would not make it an authenticated closure cache.
- `temporal_closure.update_range` is pure in-memory logical segment memoization:
  it compares program content and exact active logical facts. Its values are
  not authenticated database cache entries or exact assertion-version ledgers.
  Database range re-admission independently re-extracts/recomputes; it does not
  bypass admission merely because a logical segment was reusable.
- `catalog.PinnedGeneration` remains a storage-neutral compatibility primitive,
  with no persistent adapter consumer using it as source authentication. It is
  not a replacement for the shared source-bound adapter contracts above.
- GraphDB's bounded temporal commit/decode cache is below reasoning admission:
  it rechecks storage records/markers and store scope. Existing
  `tests/test_temporal_cache_integrity.py` and
  `tests/test_temporal_commit_cache.py` remain the relevant integrity evidence;
  a reasoning generation token cannot replace those storage checks.
- Durable reasoning state and inferred sampler files are the existing
  source-bound persistent reuse paths reviewed here. Their authenticated
  consumers are now explicit; no generic persistent closure cache was found or
  invented to inflate the migration count.

### Preserved boundaries and wider work

Source authentication, exact state compatibility and inference correctness are
separate obligations. Typed-session admission does not independently prove
closure/support soundness; finite-range re-admission additionally compares fresh
closure, and inferred hydration additionally compares the full projection.
None is independent verification of the engine implementation. Freshness at
return is not a continuing lease; current snapshots cannot be reopened by token.
Cooperating locking/CAS does not cover hostile raw storage/filesystem writers.

The original #248 acceptance and the later explicit consumer gaps are covered
by the inventory above, subject to final green review/gates. This does not
satisfy #255 typed/executor pipeline integration, #194 general native decoded
historical/concurrent-source admission, or broader phase scale/durability gates.
Indexed Python remains default; no main merge or release follows from this audit.
