# Reasoning-aware graph queries (#395)

Branch: `feature/395-reasoning-aware-queries`; PR target: `main`.

## Contract

An explicit adapter prepares a complete authenticated current-graph closure
using `execute_current_graph`. It owns the computation (never admits arbitrary
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
- [ ] Document a runnable workflow and supported/non-supported semantics.
- [ ] Run focused tests, lint, documentation and module-size checks.
- [ ] Push milestones and open a pull request; update #395 with evidence.

## Resource policy

No heavy local performance benchmarks. This is a correctness-first explicit
adapter, not a query-optimizer or speedup claim. Projection/query record limits
are not hard RSS/time limits; snapshot hashing and scalar expression allocation
retain their existing boundaries. Any scale benchmarking needed later must be
tracked on GitHub with workload and resource rationale.

## Progress

- Adapter milestone: 36 focused tests passed with LMDB on Python 3.12.
  Preparation explicitly receives both the live source graph (freshness) and
  its borrowed snapshot (authenticated extraction); these are distinct handles.
- GitHub issue comment updates are blocked by the configured token (GraphQL
  and REST both return HTTP 403). Checklist updates remain in commits; include
  this evidence and the issue linkage in the eventual pull request.
