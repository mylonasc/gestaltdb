# Native extraction original acceptance and capability audit — #194

Target: `feature/onto-dev-epic-2`, not `main`. Reference integration `96a9103`.
#194 remains open: distinguish delivered measurements and raw/decoded contracts
from any remaining general source-admitted locator/pushdown requirement. Do not
close it merely because the original remaining scale checkbox is now measured.

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

This addresses the historical locator/source correctness gap in implementation,
but acceptance is still pending green integration and an actual-adapter
measurement/decision review. Existing benchmark-only locator and full-record
native reports are not relabelled measurements of this source. Original scale
evidence remains valid for the modes it measured; do not infer a universal
locator acceleration or automatically close #194 from this additive API.

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

Closure still requires staged successful scale/repeat measurements of this actual
adapter and an explicit adopt/defer/reject review. No production default changes,
performance adoption or issue closure follows from implementing the harness or a
small smoke. Preserve all earlier raw/full-record/assertion-only results separately.

## Re-audit commands

```sh
uv run --extra backends --extra serializers --extra arrow --with 'pyrex-rocksdb==0.5.0' pytest tests/test_reasoning_native_capability_audit.py tests/test_reasoning_native_scan.py tests/test_reasoning_native_decoded.py tests/test_reasoning_native_claims.py tests/test_reasoning_extraction.py -q
uv run --extra backends --extra serializers --extra arrow --with 'pyrex-rocksdb==0.6.0' pytest tests/test_reasoning_native_capability_audit.py tests/test_reasoning_native_scan.py tests/test_reasoning_native_decoded.py tests/test_reasoning_native_claims.py tests/test_reasoning_extraction.py -q
```

These exercise the actual installed runtime. Unsupported transactional raw
tests on 0.5.0 explicitly verify rejection before skipping the unavailable
contract; a plain TransactionDB-only visibility case is separately not applicable.
No raw benchmark result is relabelled a successful database admission.
