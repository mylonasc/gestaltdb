"""RWS composed temporal/type/relation policies through external training inputs."""

import numpy as np
import pytest

from gestaltdb import TemporalContext
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import (
    ContextNegativeSampler, SamplerEngine, TemporalWalkPolicy,
    WalkPolicy, WalkSchema, iter_context_pairs,
)
from gestaltdb.serializers import PickleSerializer
from gestaltdb.versioning import NodeVersionWrite


def test_typed_temporal_stream_to_pairs_and_context_negatives(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        graph.temporal.commit_versions([
            NodeVersionWrite.assertion(Node(name, properties={"kind": kind}), (0, None))
            for name, kind in (("a", "eligible"), ("b", "eligible"), ("c", "other"))
        ])
        for edge, valid in (
            (Edge("ab", "a", "b", {"type": "R"}), (0, 10)),
            (Edge("ba", "b", "a", {"type": "R"}), (0, 20)),
            (Edge("ac", "a", "c", {"type": "S"}), (0, 20)),
            (Edge("later", "a", "c", {"type": "R"}), (10, 20)),
        ):
            graph.temporal.put_edge_version(edge, valid=valid)
        snapshot = graph.build_sampler_snapshot(tmp_path / "snapshot", temporal=True)
        a, b, c = snapshot.node_ints(["a", "b", "c"])
        relation = snapshot.relation_int("R")
        policy = WalkPolicy(avoid_recent=1, transition_unit="neighbor",
            schema=WalkSchema(node_types=[int(snapshot.node_type_ids[b])], metapath=[relation]),
            chronology=TemporalWalkPolicy(initial_time=5, max_gap_us=0))
        engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
        batches = list(engine.iter_random_walks(
            [a], 3, walks_per_node=2, batch_size=1, walk_type="policy", policy=policy,
            relations=[relation], temporal=TemporalContext.as_of(5),
        ))
        assert all(batch.node_ids.tolist() == [[a, b, a, b]] for batch in batches)
        assert all(np.all(batch.transition_times_us == 5) for batch in batches)
        negatives = ContextNegativeSampler.from_snapshot(snapshot, seed=11)
        count = 0
        for pairs in iter_context_pairs(iter(batches), window_size=1, batch_size=3):
            drawn = negatives.sample(pairs.center_nodes, positive_contexts=pairs.context_nodes,
                                     negatives_per_positive=2)
            assert np.all(drawn == c)
            count += len(pairs.weights)
        assert count == 12
        # Neither exhaustion relaxation nor waiting admits a forbidden type/relation.
        constrained = WalkPolicy(avoidance_exhaustion="relax", schema=policy.schema,
                                 chronology=TemporalWalkPolicy(initial_time=15))
        result = engine.sample_policy_walks([a], 1, policy=constrained, relations=[relation])
        assert result.lengths.tolist() == [0]
    finally:
        graph.close()


def test_chronology_fails_on_non_temporal_snapshot(tmp_path):
    from gestaltdb.sampling import SamplerSnapshot

    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "plain", external_node_ids=["a"], external_edge_ids=[],
        external_relation_ids=[], src_int=[], dst_int=[], rel_int=[],
    )
    with pytest.raises(ValueError, match="temporal snapshot"):
        SamplerEngine(snapshot).sample_policy_walks([], 0, policy=WalkPolicy(chronology=TemporalWalkPolicy()))
