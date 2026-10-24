# Generation admission contract — #248

Status: shared identity, binary point publication and typed-point admission.
#244, #245 and #246 are closed. Target: `feature/onto-dev-epic-2`.
GitHub #248 remains the authority for remaining migration scope; this is not
a claim that current-graph/range admission or all cache paths are implemented.

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
