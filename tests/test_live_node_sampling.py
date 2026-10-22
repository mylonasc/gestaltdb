"""Sampling roadmap (#106): live-KV seed sampling."""

import random

import pytest

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        db.put_nodes([
            Node("drug-1", labels=["Drug"], properties={"score": 0.9}),
            Node("drug-2", labels=["Drug"], properties={"score": 0.2}),
            Node("drug-3", labels=["Drug"], properties={"score": 0.7}),
            Node("protein-1", labels=["Protein"], properties={"score": 0.8}),
            Node("protein-2", labels=["Protein"], properties={"score": 0.1}),
            Node("other", properties={"score": 0.6}),
        ])
        yield db
    finally:
        db.close()


def test_live_node_sampling_by_label_is_reproducible(graph):
    sample = graph.sample_nodes(2, label="Drug", rng=random.Random(7))
    assert len(sample) == 2
    assert set(sample) <= {"drug-1", "drug-2", "drug-3"}
    assert sample == graph.sample_nodes(2, label="Drug", rng=random.Random(7))


def test_live_node_sampling_returns_all_eligible_when_underfilled(graph):
    assert set(graph.sample_nodes(10, label="Protein")) == {
        "protein-1", "protein-2"}
    assert set(graph.sample_nodes(10)) == {
        "drug-1", "drug-2", "drug-3", "protein-1", "protein-2", "other"}
    assert graph.sample_nodes(3, label="Missing") == []


def test_live_node_sampling_applies_node_filter(graph):
    sample = graph.sample_nodes(
        10, node_filter=lambda node: node.properties.get("score", 0) >= 0.7
    )
    assert set(sample) == {"drug-1", "drug-3", "protein-1"}

    combined = graph.sample_nodes(
        10,
        label="Drug",
        node_filter=lambda node: node.properties["score"] >= 0.7,
    )
    assert set(combined) == {"drug-1", "drug-3"}


def test_live_node_sampling_validates_arguments(graph):
    assert graph.sample_nodes(0) == []
    with pytest.raises(ValueError, match="non-negative integer"):
        graph.sample_nodes(-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        graph.sample_nodes(True)
    with pytest.raises(TypeError, match="node_filter"):
        graph.sample_nodes(1, node_filter=1)
