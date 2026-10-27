# Reasoning-aware graph queries (#395)

Branch: `feature/395-reasoning-aware-queries`; PR target: `main`.

## Contract

An explicit adapter prepares a complete authenticated current-graph closure
using `open_inferred_view` / `execute_current_graph`. It owns the computation (never admits arbitrary
caller-supplied results), borrows the caller's live read view, and exposes a
read-only Cypher surface over an explicitly selected logical graph. Each query
requires an exact generation pin; results include generation, selection and
asserted-only/asserted-plus-inferred mode. Source movement and closed/rebound
views fail closed. Ordinary `GraphDB.query` is unchanged.

The first supported subset is one `MATCH` with one node or fixed one-hop
relationship pattern, optional `WHERE`, and `RETURN` with its modifiers.
No writes, procedures, DDL, temporal qualifiers, subqueries or variable paths.
Logical relationships have set semantics, synthetic IDs and explicit origin;
physical properties, parallel-edge multiplicity and temporal claims are not
implicitly imported. Selection maps output labels/classes and types/predicates.

## Tasks

- [x] Implement bounded, authenticated preparation and explicit selection.
- [x] Reuse Cypher parser/planner/runtime without changing ordinary queries.
- [x] Test inferred-only matches, modes, provenance, freshness, limits,
      cancellation, unsupported syntax and unchanged base data.
- [x] Document a runnable workflow and supported/non-supported semantics.
- [x] Run focused tests, lint, documentation and module-size checks.
- [x] Push implementation/documentation milestones to the feature branch.
- [x] Open a pull request linked to #395: PR #426.

## Resource policy

No heavy local performance benchmarks. This is a correctness-first explicit
adapter, not a query-optimizer or speedup claim. Projection/query record limits
are not hard RSS/time limits; snapshot hashing and scalar expression allocation
retain their existing boundaries. Any scale benchmarking needed later must be
tracked on GitHub with workload and resource rationale.

## Progress

- PR #426 review: no blocking findings in the read-only query subset, logical
  projection or before/after generation/source authentication. Fresh local
  regression on Python 3.12 with LMDB and LevelDB: **759 passed, 44 optional
  skips** (query adapter, inferred views, admission and every Cypher test).
  Version advanced from 0.15.0 to 0.16.0 for the additive public API; no tag or
  PyPI publication is part of this merge.
- Adapter milestone: 36 focused tests passed with LMDB on Python 3.12.
  Preparation explicitly receives both the live source graph (freshness) and
  its borrowed snapshot (authenticated extraction); these are distinct handles.
- Documentation/failure-hardening milestone: preparation reuses the existing
  non-Cypher `open_inferred_view` rather than duplicating admission/base scans.
  Expanded new tests include mutation during execution, closure reuse, stable
  synthetic IDs, self-loops, asserted+derivable origin and both runnable examples.
- Validation: 272 passed / 179 optional-backend skips across new tests, inferred
  views, current admission, Cypher, graph interfaces and sampling. Ruff, module
  size, documentation drift and Sphinx warnings-as-errors all passed.
- Expanded regression with LMDB + LevelDB, including every `test_cypher*.py`:
  **924 passed, 102 optional-dependency skips**, Python 3.12, under one minute.
- Modular documentation examples: initial failures were missing optional plyvel;
  rerun with the LevelDB extra passed (5 passed, 3 optional-dependency skips).
  No production changes were made for missing dependencies.
- Historical submission blocker (resolved when PR #426 was created):
  GitHub issue comment updates were blocked by the configured token (GraphQL
  and REST both return HTTP 403). Checklist updates remain in commits; include
  this evidence and the issue linkage in the eventual pull request.
- PR creation also rejected by both GraphQL `createPullRequest` and REST
  `POST /repos/mylonasc/gestaltdb/pulls` with HTTP 403 (resource not accessible
  by personal access token). No PR was created. Ready-to-submit PR body is
  saved at `/tmp/opencode/395-pr.md`; no further implementation is blocked.
  After granting the GitHub CLI token pull-request/issue write permissions:

  ```sh
  gh pr create --base main --head feature/395-reasoning-aware-queries \
    --title "feat: generation-pinned reasoning-aware Cypher queries" \
    --body-file /tmp/opencode/395-pr.md
  ```
