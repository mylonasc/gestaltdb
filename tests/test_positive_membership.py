"""Persisted exact membership, legacy admission and no engine-startup sets."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from gestaltdb.sampling import HardNegativeConfig, SamplerEngine, SamplerSnapshot
from gestaltdb.sampling.positive_membership import PositiveMembership, build_membership, has_membership_encoding


def make_snapshot(path, triples=None):
    if triples is None:
        triples = [(3, 1, 2), (0, 0, 1), (3, 1, 2), (0, 0, 0), (1, 2, 3)]
    triples = np.asarray(triples, dtype=np.int64).reshape(-1, 3)
    return SamplerSnapshot.from_edge_arrays(
        path, external_node_ids=[f"n{i}" for i in range(8)],
        external_edge_ids=[f"e{i}" for i in range(len(triples))],
        external_relation_ids=["R", "S", "T"],
        src_int=triples[:, 0], rel_int=triples[:, 1], dst_int=triples[:, 2],
    )


def reseal(path, metadata=None):
    if metadata is None:
        metadata = json.loads((path / "metadata.json").read_text())
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    for name in metadata["artifacts"]:
        file = path / f"{name}.npy"
        array = np.load(file, allow_pickle=False)
        metadata["artifacts"][name] = dict(sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                                          dtype=str(array.dtype), shape=list(array.shape))
    raw = encode(metadata)
    (path / "metadata.json").write_bytes(raw)
    (path / "completion.json").write_bytes(encode(dict(
        format_version=2, complete=True, metadata_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_catalog_sha256=hashlib.sha256(encode(metadata["artifacts"])).hexdigest())))


class HashMembershipOracle:
    """Independent test-only baseline, including temporal group row numbers."""

    def __init__(self, triples):
        self.groups = {tuple(map(int, row)): index for index, row in enumerate(triples)}

    def find(self, src, rel, dst):
        return self.groups.get((src, rel, dst))


@pytest.mark.parametrize("mmap", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_persisted_membership_exact_and_borrowed_without_rebuild(tmp_path, monkeypatch, mmap, empty):
    from gestaltdb.sampling import positive_membership

    snapshot = make_snapshot(tmp_path / "s", [] if empty else None)
    loaded = SamplerSnapshot.load(snapshot.path, mmap=mmap)
    keys = loaded.positive_membership_triples
    expected = sorted(set(map(tuple, loaded.positive_triples.tolist())))
    assert keys.tolist() == [list(row) for row in expected]
    assert keys.shape == (len(expected), 3)
    if mmap:
        assert isinstance(keys, np.memmap)
        assert not keys.flags.writeable
    def forbidden(*args, **kwargs):
        pytest.fail("engine startup rebuilt persisted membership")
    monkeypatch.setattr(positive_membership, "build_membership", forbidden)
    engine = SamplerEngine(loaded, seed=15)
    assert engine._positive_membership.triples is keys
    assert not hasattr(engine, "_positive_codes")
    assert not hasattr(engine, "_positive_history_groups")
    for src in range(8):
        for rel in range(3):
            for dst in range(8):
                assert engine.is_positive(src, rel, dst) == ((src, rel, dst) in expected)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("mmap", [False, True])
def test_legacy_static_membership_fallback_once(tmp_path, monkeypatch, version, mmap):
    from gestaltdb.sampling import positive_membership

    snapshot = make_snapshot(tmp_path / "s")
    metadata = snapshot.metadata.copy()
    metadata.pop("positive_membership_encoding")
    metadata["artifacts"].pop("positive_membership_triples")
    (snapshot.path / "positive_membership_triples.npy").unlink()
    reseal(snapshot.path, metadata)
    if version == 1:
        metadata["format_version"] = 1
        metadata.pop("artifacts")
        (snapshot.path / "metadata.json").write_text(json.dumps(metadata))
    loaded = SamplerSnapshot.load(snapshot.path, mmap=mmap)
    assert loaded.positive_membership_triples is None
    original = positive_membership.build_membership
    calls = []
    def counted(triples):
        calls.append(len(triples))
        return original(triples)
    monkeypatch.setattr(positive_membership, "build_membership", counted)
    engine = SamplerEngine(loaded)
    assert engine.is_positive(3, 1, 2)
    assert not engine.is_positive(3, 1, 3)
    assert calls == [loaded.num_edges]


@pytest.mark.parametrize("corruption", ["unsorted", "duplicate", "missing_key", "extra_key", "shape", "dtype", "unknown_encoding", "missing_artifact", "checksum"])
def test_corrupt_membership_rejected_even_after_resealing(tmp_path, corruption):
    snapshot = make_snapshot(tmp_path / "s")
    file = snapshot.path / "positive_membership_triples.npy"
    keys = np.load(file)
    metadata = snapshot.metadata.copy()
    if corruption in ("unsorted", "checksum"):
        keys = keys[::-1]
    elif corruption == "duplicate":
        keys[1] = keys[0]
    elif corruption == "missing_key":
        keys = keys[:-1]
    elif corruption == "extra_key":
        keys = np.concatenate((keys, [[7, 2, 7]]))
    elif corruption == "shape":
        keys = keys.reshape(-1)
    elif corruption == "dtype":
        keys = keys.astype(np.float64)
    elif corruption == "unknown_encoding":
        metadata["positive_membership_encoding"] = "future-v99"
    else:
        metadata["artifacts"].pop("positive_membership_triples")
    np.save(file, keys)
    if corruption != "checksum":
        reseal(snapshot.path, metadata)
    with pytest.raises(ValueError):
        SamplerSnapshot.load(snapshot.path, mmap=True)


@pytest.mark.parametrize("distribution", ["uniform", "degree"])
@pytest.mark.parametrize("in_batch", [False, True])
def test_seeded_negative_rejection_matches_hash_oracle(tmp_path, distribution, in_batch):
    snapshot = make_snapshot(tmp_path / "s")
    engine, baseline = SamplerEngine(snapshot, seed=27), SamplerEngine(snapshot, seed=27)
    baseline._positive_membership = HashMembershipOracle(snapshot.positive_membership_triples)
    config = HardNegativeConfig(negatives_per_positive=3, candidate_distribution=distribution, in_batch=in_batch)
    positives = snapshot.positive_triples[[0, 1, 4]]
    a, da = engine.sample_hard_negatives(positives, set(range(8)), config, return_diagnostics=True)
    b, db = baseline.sample_hard_negatives(positives, set(range(8)), config, return_diagnostics=True)
    np.testing.assert_array_equal(a, b)
    assert da == db


def test_large_coordinate_keys_have_no_uint64_packing_limit():
    keys = np.array([[0, 2**62, 1], [2**62, 1, 2**62]], dtype=np.int64)
    index = PositiveMembership(keys)
    assert index.find(0, 2**62, 1) == 0
    assert index.find(2**62, 1, 2**62) == 1
    assert index.find(2**62, 2, 0) is None


def test_randomized_sorted_membership_matches_set_oracle():
    rng = np.random.default_rng(429)
    triples = rng.integers(0, 20, (500, 3), dtype=np.int64)
    index = PositiveMembership(build_membership(triples))
    oracle = set(map(tuple, triples.tolist()))
    for src, rel, dst in rng.integers(0, 20, (1000, 3)):
        assert (index.find(src, rel, dst) is not None) == ((src, rel, dst) in oracle)


def test_missing_encoded_keys_do_not_silently_rebuild(tmp_path):
    snapshot = make_snapshot(tmp_path / "s")
    with pytest.raises(ValueError, match="lacks positive membership"):
        SamplerEngine(replace(snapshot, positive_membership_triples=None))


def test_membership_marker_requires_supported_envelope_and_temporal_history():
    with pytest.raises(ValueError, match="unsupported positive membership"):
        has_membership_encoding(dict(format_version=1, positive_membership_encoding="lexicographic-triples-v1"))
    with pytest.raises(ValueError, match="requires temporal history"):
        has_membership_encoding(dict(format_version=2, positive_membership_encoding="lexicographic-triples-v1",
                                     temporal=True))


@pytest.mark.parametrize("saturated", [False, True])
def test_exhaustion_matches_hash_oracle(tmp_path, saturated):
    # The sampler may try BOTH head and tail corruption, regardless of the
    # initial direction probability. Saturate both directions for this fixture.
    triples = [(src, 0, dst) for src in range(8) for dst in range(8)
               if saturated or (src, dst) != (0, 7)]
    snapshot = make_snapshot(tmp_path / "s", triples)
    engine, baseline = SamplerEngine(snapshot, seed=35), SamplerEngine(snapshot, seed=35)
    baseline._positive_membership = HashMembershipOracle(snapshot.positive_membership_triples)
    config = HardNegativeConfig(negatives_per_positive=3, head_probability=0.0, exhaustion_policy="repeat")
    def sample(engine):
        return engine.sample_hard_negatives([[0, 0, 1]], set(range(8)), config, return_diagnostics=True)
    if saturated:
        for sampler in (engine, baseline):
            with pytest.raises(RuntimeError, match="failed to sample"):
                sample(sampler)
    else:
        a, da = sample(engine)
        b, db = sample(baseline)
        np.testing.assert_array_equal(a, b)
        assert da == db
        assert da["repeated"] > 0


def test_append_prune_and_weight_rewrites_refresh_persisted_membership(tmp_path):
    from gestaltdb.graphdb import Edge
    from .test_sampler_consistency import _FakeVersionedGraph

    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "s")
    graph.add_edge(Edge("new", "drug-2", "protein-1", {"type": "drug-to-protein"}))
    snapshot.append_from_graph(graph, [b"new"])
    key = (snapshot.node_int("drug-2"), snapshot.relation_int("drug-to-protein"), snapshot.node_int("protein-1"))
    assert SamplerEngine(snapshot).is_positive(*key)
    graph.remove_edge("new")
    snapshot.compact(graph)
    assert not SamplerEngine(snapshot).is_positive(*key)
    snapshot.set_edge_weights(snapshot.edge_weights.copy(), source="test")
    assert snapshot.metadata["positive_membership_encoding"] == "lexicographic-triples-v1"
    np.testing.assert_array_equal(snapshot.positive_membership_triples, build_membership(snapshot.positive_triples))


@pytest.mark.parametrize("name", ["dense_v1", "dense_v2", "temporal_v2"])
@pytest.mark.parametrize("mmap", [False, True])
def test_released_wheel_membership_matches_direct_edge_scan(name, mmap):
    """Exercise legacy admission with actual released bytes, not downgrades."""
    from gestaltdb import TemporalContext

    path = Path(__file__).parent / "fixtures" / "released_snapshots" / name
    snapshot = SamplerSnapshot.load(path, mmap=mmap)
    engine = SamplerEngine(snapshot, seed=7)
    triples = snapshot.positive_triples.tolist()
    if snapshot.temporal:
        assert engine._positive_membership.triples is snapshot.positive_history_triples
    else:
        assert snapshot.positive_membership_triples is None

    for src in range(snapshot.num_nodes):
        for rel in range(snapshot.num_relations):
            for dst in range(snapshot.num_nodes):
                matching = [i for i, triple in enumerate(triples) if triple == [src, rel, dst]]
                assert engine.is_positive(src, rel, dst) == bool(matching)
                if not snapshot.temporal:
                    continue
                for instant in (0, 50, 100, 150, 200):
                    expected = any(
                        snapshot.valid_from_us[i] <= instant
                        and (snapshot.valid_to_open[i] or instant < snapshot.valid_to_us[i])
                        for i in matching
                    )
                    assert engine.is_positive(
                        src, rel, dst, policy="at_positive_time",
                        temporal=TemporalContext.as_of(instant),
                    ) == expected
                for policy in ("overlap", "contained"):
                    expected = any(
                        (snapshot.valid_from_us[i] < 160
                         and (snapshot.valid_to_open[i] or 120 < snapshot.valid_to_us[i]))
                        if policy == "overlap" else
                        (120 <= snapshot.valid_from_us[i]
                         and not snapshot.valid_to_open[i] and snapshot.valid_to_us[i] <= 160)
                        for i in matching
                    )
                    assert engine.is_positive(
                        src, rel, dst, policy="window", window_policy=policy,
                        temporal=TemporalContext.during(120, 160),
                    ) == expected
