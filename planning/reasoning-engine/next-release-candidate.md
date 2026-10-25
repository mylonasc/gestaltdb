# Next release candidate — #385

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
- [ ] Full optional local suite with PyRex 0.6.0.
- [ ] Docs/API drift, runnable examples, Sphinx warnings-as-errors.
- [ ] Wheel/sdist metadata validation and isolated installed-artifact checks.
- [ ] Main-target draft PR with all applicable CI, including general tests,
      optional dependencies, security, visualization, and fuzzing.
- [ ] Decide next version (recommend another prerelease, not stable promotion).
- [ ] Final exact-head approval; revalidate any changed candidate.
- [ ] Only after explicit merge/publish authorization: exact merged-main push
      checks, then release publication and installed-distribution smoke tests.

Evidence and outstanding failures are recorded in #385. Historical green jobs
and published rc1 do not certify this candidate.
