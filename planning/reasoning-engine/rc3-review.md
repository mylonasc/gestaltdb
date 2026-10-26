# 0.14.0rc3 review and release gates

This candidate promotes `feature/onto-dev-epic-2` after the #393 and #396
acceptance fixes and includes main's #411/#412 stratified aggregate
proof-collector fix. The integration branch was recreated from the rc2 main
commit because the original epic branch had been removed after promotion.

## Scope

- #393: `TemporalClosure` separates complete logical conclusions from bounded
  derivations. Proof-budget exhaustion preserves answers, warns with
  `TemporalProofTruncatedWarning`, and records missing evidence.
- #396: typed graph projection fails closed on identity collisions by default.
  `identity_collision="merge"` is an explicit entity-resolution opt-in, and
  source references use canonical JSON components to prevent aliasing.
- #411/#412: stratified aggregate evaluation forwards its proof collector so
  aggregate conclusions carry witnesses.

## Pre-release local evidence

Python 3.12 with LMDB and LevelDB:

- Reasoning/ontology suite: **1,362 passed, 406 skipped**.
- Independent #393/#396 acceptance probes: **8 passed**; the proof-budget
  probe warns explicitly as intended.
- Ruff, docs/API drift, and executable examples: pass.
- Built `gestaltdb-0.14.0rc3` wheel and sdist; `twine check` passes both.

Full exact-head CI and installed-artifact checks remain the merge gate; local
evidence alone is not release certification.

## Deliberate boundaries

Temporal proof completeness does not imply exhaustive explanation enumeration.
Typed identity merging is an explicit projection choice, not logical `sameAs`,
and it never relaxes reference traceability. Finite evaluation, structural
validation, and ontology profiles retain the rc2 boundaries: no unbounded
streaming, full SHACL, full OWL, or universal durability guarantee.
