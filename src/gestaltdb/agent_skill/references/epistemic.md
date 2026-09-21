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
    finally:
        graph.close()
```
