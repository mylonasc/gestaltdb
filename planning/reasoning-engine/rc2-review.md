# 0.14.0rc2 review and release gates

This candidate combines current main (service decomposition and offline
reasoning visualization) with integration features #392–#396, reviewed PRs
#304/#369 and dependency updates #316–#322. Consolidated integration PR: #405.
The user authorized review fixes, integration, conditional main merge and a new
release candidate. This record supersedes the older next-candidate exclusion of
#369, not its evidence limitations or the reliability scope in #381.

## Review fixes

- Migrated locator implementation, fault/coverage tests and executable guide to
  scoped temporal/epistemic services after main removed GraphDB wrappers.
- Prevented foreign issue numbers from aliasing local atlas relationships, with
  a regression test; kept ordinary references distinct from dependencies.
- Preserved typed proof behavior and main's executable docstrings together.
- Extracted typed result values and tightened the module-size baseline; existing
  public imports remain available. Retired planning documents stay deleted.

## Pre-release local evidence

Python 3.12, all three real embedded backends, four serializers, PyRex 0.6.0,
Arrow/Polars, and the pinned independent ontology oracles:

- Full combined source suite: **3,597 passed, 3 skipped**.
- Ruff, docs/API drift, 8/8 executable examples, roadmap, Sphinx `-W`: pass.
- Packaged visualization reproducible bundle/license checks: pass.
- Offline visualization Playwright: **18/18** pass.
- Portable issue-atlas offline browser smoke: pass, no JavaScript errors.

Final release requires exact-head CI, built wheel/sdist metadata validation,
isolated artifact/baseline database checks, merged-main checks and post-publish
installed-wheel smoke. GitHub PR/run/release records retain the final outcomes;
the local evidence above alone is not release certification.

## Deliberate boundaries

Native locator prefixes reduce row transfer but complete catalog authentication
and canonical conformance still read/decode unrelated records. No measured
actual-adapter speed/RSS claim, automatic source/executor change, transactional
native admission, or persistent native planner. #194 remains open.

Typed proof witnesses and their verification do not imply exhaustive explanation
enumeration. Finite temporal partition evaluation does not imply unbounded
streaming. Structural validation is not full SHACL, and the ontology profiles
are not full OWL. Earlier crash fixtures do not establish arbitrary publisher
termination, power-loss, torn-page or network-filesystem durability. Remaining
roadmap work and stronger validation evidence are not advertised as delivered.
