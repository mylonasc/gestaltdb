# Native extraction original acceptance and capability audit — #194

Historical audit baseline: integration `96a9103`, originally targeting
`feature/onto-dev-epic-2`. That target is fulfilled history, not current work
instructions. Current authority is `EPIC_REASONING_ENGINE.md` sections 1–4.

Current reconciliation (2026-11-01T23:07:18Z): #450 and HSA-02 #461 are merged
to main `dcc90e2`; the earlier integration blocker below is resolved. #194 stays
open for closure reconciliation, not because its source or measurements are
missing. HSA-03/HSA-04 are separately on `hsa-dev` (`af08f60`), with HSA-06 pilot
evidence in `planning/reasoning-followthrough/hsa-06-pilot.md`. Dense construction
clears the preserved 900s gate, but selective native latency/RSS still misses the
ordinary-relative goal. Native adoption and optional new-index HSA-05 remain
deferred. Original #450 reports retain their original hashes and limitations.

Superseded review against main `7f28808` (sanitation #452) on 2026-11-01: the production
locator source is already on main. Open PR #450 contains actual-adapter evidence
and a **defer automatic adoption** decision, not yet merged into reviewed main.
It preserves 18/18 100K sparse-history and 36/36 diagnostic dense-history 10K
exact fact/version-ID parity observations plus the separate dense 100K
construction timeout. No rule-closure, repeated-distribution or OS-cold claim is
made by those extraction results. #194 awaits integration/review; #451 separately
owns dense-history supersession/authentication and locator read-amplification
remediation. Do not turn a measured defer decision into a speedup/default change.

## Original issue requirements, not merged-child counts

| Original #194 item | Delivered evidence | Boundary / remaining acceptance |
|---|---|---|
| Native temporal index-locator scan + point materialization; compare full scan | `native.py:iter_locator_keys`; real temporal predicate-index and native full-record modes in `reasoning_frontier_pipeline.py`; #342/#349 reports at clean `a1cfceb`, including repeated 100K variable IDs/provenance | Benchmark decoder is authenticated but **quiescent assertion-only**, not general historical locator admission; other backends have no native Arrow entry point and must not silently fall back |
| Decoded Evidence with exact IDs, predicate/binding filters and authenticated visibility | `iter_decoded_evidence` generic bounded caller-owned decoder; explicit `PinnedNativeClaimSource` from #366/#367 authenticates complete record coverage, historical corrections/retractions, canonical grouped point conformance and shared claim policy/context | Generic `visibility` strings are caller assertions, not independent source admission. Full admitted source filters **after** canonical selection, not native storage predicate/binding pushdown. General source-admitted native locator/pushdown remains a genuine gap if required by this item |
| 100K+ variable-value, cache-aware measurements before performance claims | #313 retained failures/synthetic raw measurements; #315 grouped ordinary source rerun (120 parity successes); #349 real recursive native/locator decision (144 observations, 18 bounded Polars outcomes); #365 explicit-budget pipeline (48 parity successes); #367 admitted-source baseline (15 parity successes + 120s extraction timeout) and corrected 40/40 parity observations including repeated 100K selective/sparse | Larger evidence is now present. First-reopened/primed is **not OS-cold**, payload padding is compressible, copied bytes are uninstrumented, RSS is whole-worker. No universal acceleration or cold-cache claim follows; native default/adoption stays unchanged |
| Re-audit upstream TransactionDB / column families, update capabilities and gates | Original 0.5.0 absence retained. 0.6.0 raw transactional default-family scans now pass snapshot sharing, range, lifetime, bounded rows and committed/uncommitted/rollback visibility gates; named families remain rejected | `ExtractionCapability` describes raw entry points only; no automatic transactional authenticated claim-source admission or arbitrary column-family scan API |
| Decide GenerationStore/native planning ownership | Existing RE-26 no-bespoke-kernel decision; logical generation/context identity and explicit application admission are separate #248/#255 deliverables | No invented persistent native plan cache or custom runtime merely to mark this item complete |

## Delivered source and measured decision

The full admitted native path is additive and explicit, preserving inward-only
core/compiler boundaries. It authenticates visible commit records, exact key/
ordinal/bytes, complete expected raw coverage and final eligible histories;
canonical point selection must match exactly before shared policy/mapping yields
version-ID evidence. Corruption, omitted/duplicate/stale canonical results,
rebindings, cancellation, row budgets, historical appends, interrupted-prefix
gaps and concurrent cooperating writers have fail-closed tests. One grouped
canonical iterator replaces N point operations without skipping full commit
integrity or successful-exhaustion checks. Four serializer/restart and multistep
bounded-witness tests preserve semantics, not all-support provenance.

Corrected 100K native totals remain slower/higher-RSS than ordinary extraction.
The performance decision is **no adoption**, not a new native rewrite or a
promise that greater scale will reverse the result. Full runtime/source/hash,
median/P95 and accounting boundaries are in `docs/reasoning-native-claims.rst`.

## Avoid unbounded acceptance expansion

`pipeline-benchmarks.md` is a broader experimental register, not a silent
replacement of the original issue. It additionally mentions support discovery,
managed publication, oversized-value rejection studies, transactional source
expansion and privileged cold-cache testing. Those stages are not certified by
successful extraction/recursive closure or raw snapshots. Keep their deferrals
visible and relate them to #156's actual phase/workload gates; do not invent a
new consumer or automatically weaken semantics to fill every experimental row.

Conversely, a benchmark-only locator is not production historical locator
admission, and post-decode/post-selection filters must not be renamed physical
storage pushdown. Those are the specific original/native-source boundaries to
reconcile before #194 closure, separately from raw capability availability.

## Historical locator continuation (post-#368)

`PinnedNativeLocatorClaimSource` now implements a separate explicit production
source: native world/predicate/subject-prefix candidate intersection, complete
logical-history locator scans (including retractions), point materialization and
exact expected index coverage from the complete authenticated pinned catalog.
It retains grouped canonical conformance and shared policy/context/evidence;
object/agent/source/derived filters are still post-selection. Its physical
prefix pushdown must not be described as eliminating full catalog decoding or
canonical reads. Dedicated catalog-record and cumulative native-index-row
limits/profiles expose the different work units.

This addressed the historical locator/source correctness gap and reached main
through #369. Actual-adapter measurements/decision are delivered separately in
PR #450, now merged; issue closure reconciliation remains. Existing benchmark-only
locator and full-record native reports are not relabelled measurements of this
source. Original scale evidence remains valid for the modes it measured; do not
infer a universal acceleration or automatically close #194 from this additive API.

## Actual-adapter benchmark continuation (2026-11-01)

The current issue body targets `main`; the integration-only target at the top of
this document is historical. New work starts from `3403530` on dedicated branch
`feature/194-native-locator-benchmarks`.

`benchmarks.reasoning_locator_pipeline` now compares the production historical
locator adapter, authenticated full-record adapter and ordinary source in separate
fresh workers. It covers nonselective, predicate and subject requests; correction
and retraction windows; exact write-receipt-derived fact/version parity; full
authentication/materialization costs; native work profiles; runtime/source hashes;
and timing/whole-worker RSS. See `docs/reasoning-native-locator-claims.rst` for commands.
This is extraction-only, not a relabelling of inference/support/publication results.

The actual-adapter scale comparison and explicit defer decision are now recorded
in `docs/reasoning-native-locator-claims.rst`, with committed reports under
`benchmarks/results/reasoning-194-locator-*`. PR #450 integration/review is now
complete; closure reconciliation is not blocked on #451 optimization debt. Repeated isolated measurements
remain necessary for a future adoption claim; single observations suffice to keep
the existing default and explicitly defer adoption. Preserve earlier raw/full-record/
assertion-only results separately and distinguish dense construction failure from
sparse-history extraction success. The 10K report's later-worker harness imports
have a documented provenance qualification, not immutable-source certification.

## Re-audit commands

```sh
uv run --extra backends --extra serializers --extra arrow --with 'pyrex-rocksdb==0.5.0' pytest tests/test_reasoning_native_capability_audit.py tests/test_reasoning_native_scan.py tests/test_reasoning_native_decoded.py tests/test_reasoning_native_claims.py tests/test_reasoning_extraction.py -q
uv run --extra backends --extra serializers --extra arrow --with 'pyrex-rocksdb==0.6.0' pytest tests/test_reasoning_native_capability_audit.py tests/test_reasoning_native_scan.py tests/test_reasoning_native_decoded.py tests/test_reasoning_native_claims.py tests/test_reasoning_extraction.py -q
```

These exercise the actual installed runtime. Unsupported transactional raw
tests on 0.5.0 explicitly verify rejection before skipping the unavailable
contract; a plain TransactionDB-only visibility case is separately not applicable.
No raw benchmark result is relabelled a successful database admission.
