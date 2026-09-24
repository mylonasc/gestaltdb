import numpy as np

from gestaltdb import ClaimStatus, HardNegativeConfig, SamplerEngine, TemporalContext
from gestaltdb.graphdb import Edge, Node
from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite


def test_end_to_end_temporal_epistemic_workflow(graph_db, tmp_path):
    graph_db.commit_versions([
        NodeVersionWrite.assertion(Node("alice", labels=["Person"]), (0, None)),
        NodeVersionWrite.assertion(Node("acme", labels=["Company"]), (0, None)),
        NodeVersionWrite.assertion(Node("industry", labels=["Group"]), (0, None)),
        NodeVersionWrite.assertion(Node("other", labels=["Company"]), (0, None)),
        EdgeVersionWrite.assertion(
            Edge("employment", "alice", "acme", {"type": "WORKS_FOR"}),
            (0, None),
        ),
    ])

    matched = graph_db.query(
        "MATCH (a {id: 'alice'})-[r:WORKS_FOR]->(b) "
        "FOR VALID_TIME AS OF datetime($at) RETURN a.id AS person, b.id AS company",
        {"at": "1970-01-01T00:00:00.000010Z"},
    )
    assert matched.records == [{"person": "alice", "company": "acme"}]

    snapshot = graph_db.build_sampler_snapshot(
        tmp_path / "temporal-snapshot", temporal=True, time_bucket="none"
    )
    engine = SamplerEngine(snapshot, seed=17)
    node_ids = snapshot.external_node_ids.tolist()
    relation = snapshot.external_relation_ids.tolist().index("WORKS_FOR")
    alice = node_ids.index("alice")
    acme = node_ids.index("acme")
    sampled = engine.sample_neighbors(
        [alice], fanout=1, relations=[relation], temporal=TemporalContext.as_of(10)
    )
    assert sampled.neighbor_nodes.tolist() == [acme]
    negatives = engine.sample_hard_negatives(
        np.asarray([[alice, relation, acme]], dtype=np.int64),
        config=HardNegativeConfig(
            negatives_per_positive=1,
            head_probability=0.0,
            temporal_positive_policy="at_positive_time",
        ),
        positive_times_us=[10],
    )
    assert negatives.shape == (1, 1, 3)
    assert not engine.is_positive(
        *negatives[0, 0], temporal=TemporalContext.as_of(10), policy="at_positive_time"
    )

    employment = graph_db.assert_claim(
        subject="alice", predicate="WORKS_FOR", object="acme",
        polarity="positive", agent="source:hr", world="verified", valid=(0, None),
    )
    graph_db.assert_claim(
        subject="acme", predicate="MEMBER_OF", object="industry",
        polarity="positive", agent="source:registry", world="verified", valid=(0, None),
    )
    graph_db.create_rule(
        "employment-implies-affiliation",
        when=[("?person", "WORKS_FOR", "?company"), ("?company", "MEMBER_OF", "?group")],
        then=("?person", "AFFILIATED_WITH", "?group"),
    )
    run = graph_db.run_rules(as_of=10, world="verified")
    assert run.derived_count == 1
    derived = run.versions[0]
    graph_db.assert_world_accessibility(
        agent="auditor", from_world="actual", to_world="verified",
        kind="knowledge", valid=(0, None),
    )
    entailed = graph_db.entails(
        "auditor",
        {"subject": "alice", "predicate": "AFFILIATED_WITH", "object": "industry"},
        "KNOWS", world="actual", valid_time=10, max_depth=4, max_states=100,
    )
    assert entailed.status is ClaimStatus.SUPPORTED
    assert entailed.explanation["evaluation"]["children"][0]["evidence"][0]["versionId"] == derived.version_id

    graph_db.retract_claim(
        employment.logical_id,
        supersedes_version_id=employment.version_id,
        reason="source withdrawal",
    )
    maintained = graph_db.maintain_truth()
    assert maintained.retracted_count == 1
    assert graph_db.entails(
        "auditor",
        {"subject": "alice", "predicate": "AFFILIATED_WITH", "object": "industry"},
        "KNOWS", world="actual", valid_time=10, max_depth=4, max_states=100,
    ).status is ClaimStatus.UNKNOWN
