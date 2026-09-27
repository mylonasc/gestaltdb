# Epistemic Claims For Library Users

Read this topic when storing sourced assertions or denials, querying them at
valid/system time, or distinguishing contradictory evidence from uncertainty.

## Rules

- `Claim.statement_id` deterministically identifies the subject-predicate-object proposition.
- `Claim.claim_id` also includes polarity, agent, source, and world. Confidence and provenance do not affect identity.
- `polarity` is `"positive"` or `"negative"`; `confidence` is independently `None` or a finite number from 0 through 1.
- Objects are entity IDs by default. Use `object_kind="literal"` for canonical JSON-compatible literals.
- Claims append to temporal history and do not create current-state graph edges or participate in Cypher matching.
- Use `get_claim_as_of` for one claim identity and `iter_claims_as_of` for indexed statement/source/agent/world/polarity lookup.
- Use `correct_claim` to revise confidence or provenance and `retract_claim` to withdraw a claim over a valid interval. Both preserve history.
- `claim_status` returns `ClaimStatus.SUPPORTED`, `REFUTED`, `BOTH`, or `UNKNOWN` under open-world semantics.
- Pass either `system_time` or `through_commit` for historical knowledge. A `GraphReadView` pins the horizon and supplies a default valid time.
- Deferred claim writes make the temporal index family stale; rebuild the temporal indexes before indexed reads.
- `create_rule(name, when=[...], then=...)` versions safe positive Horn rules; head variables must be bound in the body and predicates must be constants.
- `run_rules(as_of=...)` joins positive entity claims within one world, intersects premise validity, and stores conclusions under the `gestaltdb:rules` agent with rule/premise version justifications.
- Set `max_iterations`, `max_derivations`, and `max_justifications` for workload bounds. A limit error persists no partial conclusions, and an unchanged repeat run is idempotent.
- Call `maintain_truth(as_of=...)` after premise or rule changes. It keeps independent supports, corrects changed justifications, and retracts a conclusion only after its final support disappears. Later calls can omit `as_of` to resume the previous valid time.
- `explain_claim(claim_id, valid_time=..., system_time=...)` returns exact claim/rule version nodes and ordered derivation edges. Use `max_depth` and `max_nodes` to bound traversal; inspect `truncated` to detect a reached bound.
- `assert_world_accessibility(agent=..., from_world=..., to_world=..., kind=...)` stores positive temporal links in independent `belief`, `knowledge`, and `modal` frames.
- `entails(agent, proposition, mode, world=..., valid_time=...)` evaluates `BELIEVES`, `KNOWS`, `POSSIBLE`, or `NECESSARY` in one pinned read view. Always set finite `max_depth` and `max_states` appropriate to the workload.
- Modal formulas accept atoms plus `NOT`, `AND`, `OR`, and nested modal operators. They use four-valued paraconsistent semantics; missing accessibility is `unknown`, and contradictions do not entail unrelated atoms.
- `CALL kg.entails(...) YIELD status, confidence, explanation RETURN ...` exposes the same bounded evaluator. Options require `world` and `validTime`; `systemTime` selects historical knowledge.

## Example

```python
from tempfile import TemporaryDirectory

from gestaltdb import ClaimStatus
from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(tmpdir, backend="leveldb", serializer="json")
    try:
        positive = graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="positive", agent="source:hr", source="hr.csv",
            confidence=0.97, world="reported",
            valid_from="2024-01-01T00:00:00Z",
        )
        graph.assert_claim(
            subject="alice", predicate="WORKS_FOR", object="acme",
            polarity="negative", agent="source:audit", confidence=0.7,
            world="reported", valid_from="2024-01-01T00:00:00Z",
        )
        assert graph.claim_status(
            "alice", "WORKS_FOR", "acme",
            valid_time="2024-06-01T00:00:00Z", world="reported",
        ) is ClaimStatus.BOTH

        graph.correct_claim(
            positive.logical_id,
            supersedes_version_id=positive.version_id,
            confidence=0.99,
            provenance={"reviewed": True},
        )

        graph.assert_claim(
            subject="acme", predicate="MEMBER_OF", object="industry",
            polarity="positive", agent="source:registry", world="reported",
            valid_from="2024-03-01T00:00:00Z",
        )
        graph.create_rule(
            "employment-implies-affiliation",
            when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
            then=("?p", "AFFILIATED_WITH", "?g"),
        )
        result = graph.run_rules(as_of="2024-06-01T00:00:00Z")
        assert result.derived_count == 1
        derived = result.versions[0]
        explanation = graph.explain_claim(
            derived.logical_id, valid_time="2024-06-01T00:00:00Z"
        )
        assert explanation.root_version_id == derived.version_id
    finally:
        graph.close()
```
