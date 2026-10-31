# Next release candidate — #385

> Historical preparation snapshot: PR #386 subsequently merged into `main`;
> the package has since shipped through v0.16.0. The unchecked candidate gates
> and old branch/version recommendations below are not current release tasks.
> #385 was reconciled and closed as superseded on 2026-11-01.

Preparation branch: `release/reasoning-next-candidate`.
Inputs: integration `b5d21bd` and main `72e320f`. Main integrated without
conflicts; its retired `ONTO-EPIC.md` deletion and replacement references remain.
No version bump, main merge, tag, or publication is authorized by this record.

## Candidate contract

This is post-rc1 hardening and explicit adapter composition, not a new default
executor or a full-OWL/SHACL, general zero-copy, or universal durability release.
Optional locator PR #369 is excluded. Existing databases and v1 session files
must retain their documented reopen and guarded-resume behavior.

Signed-denial rule premises, datatype shapes, reusable TBox closure, streaming
validation, and exact validation claim-version provenance are not delivered.
Source authentication, recomputed closure comparison, bounded witnesses, and
independent proof verification are distinct guarantees.

## Evidence boundaries requiring particular care

Issue closure is not release certification. The #370 crash tests cover a durable
sequence reservation, a manually staged session temporary file, and a source
commit between range staging and publication. They do **not** kill the actual
publisher during descriptor/record/index/visibility-marker writes, nor invoke
the actual session saver across its replacement boundary. Keep broader claims
excluded; the scope decision in #381 does not turn these into tested behavior.

The #374 point-agreement fixture compares two range evaluations, not an
independent point evaluator. The #371 LRU fixture checks revision equality,
which alone does not prove retained object identity or distinguish recompilation.
These are validation-quality gaps to assess before advertising stronger claims.

## Gates

- [x] Merge current main and retain its documentation deletion.
- [x] Full optional local suite with PyRex 0.6.0: 3,354 passed / 3 skipped
      on the main-integrated source tree (62.87 seconds).
- [x] Docs/API drift, 8/8 runnable examples, Sphinx warnings-as-errors.
- [ ] Wheel/sdist metadata validation and isolated installed-artifact checks.
- [ ] Main-target draft PR with all applicable CI, including general tests,
      optional dependencies, security, visualization, and fuzzing.
- [ ] Decide next version (recommend another prerelease, not stable promotion).
- [ ] Final exact-head approval; revalidate any changed candidate.
- [ ] Only after explicit merge/publish authorization: exact merged-main push
      checks, then release publication and installed-distribution smoke tests.

Evidence and outstanding failures are recorded in #385. Historical green jobs
and published rc1 do not certify this candidate.

## Preliminary artifact evidence

At `25e3405`, `uv build` built wheel and sdist; `twine check` passed both.
An isolated Python 3.12 wheel install outside the checkout passed binary closure,
new adapter imports, and packaged quickstart retrieval. Metadata deliberately
still says `0.13.0rc1`: these are unpublished preparation artifacts, not rc1
replacements. Rebuild and revalidate after the next version is chosen. Full
supported-Python/compatibility artifact checks remain a final gate.

Draft main-target PR: #386. Preparation issue: #385, under #156.

## Pre-merge CI policy

PR Python matrices run 3.10 and 3.13, the supported edge versions; push/manual
matrices keep 3.10–3.13. Each selected job still runs its complete suite, with
real backend preflight, independent oracles, skip reports, and installed-artifact
compatibility checks retained. The broad optional-integrations job uses 3.13.

Code/dependency/test changes select code suites. README, Sphinx prose/assets,
packaged Markdown help, and roadmap/benchmark reports alone do not select broad
code workflows. Python-file docstring changes are conservatively treated as
code. Documentation still builds with warnings-as-errors and checks API/roadmap
references. Superseded PR runs can cancel; unrelated PR documentation builds no
longer queue behind Pages deployment. Post-merge runs are not cancelled by this
policy. Actionlint and ``tests/test_ci_workflow_policy.py`` validate the policy.
