import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInstant


class _MetadataStore:
    supports_transactions = False

    def __init__(self):
        self.metadata = {}

    def get_metadata(self, key):
        return self.metadata.get(key)

    def put_metadata(self, key, value):
        self.metadata[key] = value

    def close(self):
        pass


@pytest.fixture
def graph():
    return GraphDB(_MetadataStore(), JSONSerializer())


def test_as_of_overlay_respects_valid_intervals_and_system_horizons(graph):
    asserted = graph.put_node_version(Node("alice", properties={"name": "Alice"}), valid=(0, 100))
    corrected = graph.correct_node_version(
        Node("alice", properties={"name": "Alicia"}),
        supersedes_version_id=asserted.version_id,
        valid=(20, 40),
    )
    retracted = graph.retract_node_version(
        "alice", supersedes_version_id=corrected.version_id, valid=(30, 35)
    )

    assert graph.get_node_as_of("alice", valid_time=10).version_id == asserted.version_id
    assert graph.get_node_as_of("alice", valid_time=25).version_id == corrected.version_id
    assert graph.get_node_as_of("alice", valid_time=32) is None
    assert graph.get_node_as_of("alice", valid_time=37).version_id == corrected.version_id
    assert graph.get_node_as_of("alice", valid_time=50).version_id == asserted.version_id
    assert graph.get_node_as_of(
        "alice", valid_time=32, through_commit=asserted.commit_id
    ).version_id == asserted.version_id
    assert graph.get_node_as_of(
        "alice", valid_time=25, system_time=asserted.system_time
    ).version_id == asserted.version_id
    assert graph.get_node_as_of(
        "alice",
        valid_time=25,
        system_time=TemporalInstant(corrected.system_time.epoch_microseconds - 1),
    ).version_id == asserted.version_id
    assert retracted.commit_id > corrected.commit_id


def test_as_of_uses_half_open_boundaries_and_rejects_conflicting_horizons(graph):
    graph.put_node_version(Node("n1"), valid=(10, 20))

    assert graph.get_node_as_of("n1", valid_time=9) is None
    assert graph.get_node_as_of("n1", valid_time=10) is not None
    assert graph.get_node_as_of("n1", valid_time=19) is not None
    assert graph.get_node_as_of("n1", valid_time=20) is None
    with pytest.raises(ValueError, match="either system_time or through_commit"):
        graph.get_node_as_of("n1", valid_time=10, system_time=0, through_commit=1)


def test_typed_temporal_traversal_resolves_corrected_topology_and_retractions(graph):
    first = graph.put_edge_version(
        Edge("e1", "alice", "acme", {"type": "WORKS_FOR"}), valid=(0, 100)
    )
    changed = graph.correct_edge_version(
        Edge("e1", "alice", "other", {"type": "ADVISES"}),
        supersedes_version_id=first.version_id,
        valid=(20, 40),
    )
    graph.put_edge_version(
        Edge("e2", "alice", "acme", {"type": "WORKS_FOR"}), valid=(0, None)
    )
    graph.retract_edge_version(
        "e2", supersedes_version_id=next(v for v in graph.iter_edge_versions("e2")).version_id,
        valid=(30, 35),
    )

    at_10 = list(graph.iter_edges_as_of(source="alice", edge_type="WORKS_FOR", valid_time=10))
    at_25 = list(graph.iter_edges_as_of(source="alice", edge_type="ADVISES", valid_time=25))
    at_32 = list(graph.iter_edges_as_of(source="alice", edge_type="WORKS_FOR", valid_time=32))
    at_50 = list(graph.iter_edges_as_of(source="alice", edge_type="WORKS_FOR", valid_time=50))
    incoming = list(graph.iter_edges_as_of(target="other", direction="in", edge_type="ADVISES", valid_time=25))

    assert [version.logical_id for version in at_10] == ["e1", "e2"]
    assert [version.version_id for version in at_25] == [changed.version_id]
    assert at_32 == []
    assert [version.logical_id for version in at_50] == ["e1", "e2"]
    assert [version.version_id for version in incoming] == [changed.version_id]
