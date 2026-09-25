import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.ingestion import IndexMaintenanceMode
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInstant
from gestaltdb.versioning import NodeVersionWrite


class _MetadataStore:
    supports_transactions = False

    def __init__(self):
        self.metadata = {}
        self.indexes = {}
        self.range_indexes = {}

    def get_metadata(self, key):
        return self.metadata.get(key)

    def put_metadata(self, key, value):
        self.metadata[key] = value

    def put_index_entry(self, name, parts, value):
        self.indexes.setdefault((name, tuple(parts)), set()).add(value)

    def put_index_entries_bulk(self, entries):
        for name, parts, value in entries:
            self.put_index_entry(name, parts, value)

    def iter_index_prefix(self, name, parts):
        return iter(sorted(self.indexes.get((name, tuple(parts)), set())))

    def put_range_index_entry(self, name, parts, range_value, value):
        self.range_indexes.setdefault((name, tuple(parts)), set()).add((range_value, value))

    def put_range_index_entries_bulk(self, entries):
        for name, parts, range_value, value in entries:
            self.put_range_index_entry(name, parts, range_value, value)

    def iter_range_index(self, name, parts, start=None, end=None, include_start=True, include_end=True, *, reverse=False, limit=None):
        values = []
        for range_value, value in sorted(self.range_indexes.get((name, tuple(parts)), set())):
            if start is not None and (range_value < start or (range_value == start and not include_start)):
                continue
            if end is not None and (range_value > end or (range_value == end and not include_end)):
                continue
            values.append(value)
        if reverse:
            values.reverse()
        if limit is not None:
            values = values[:limit]
        return iter(values)

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


def test_system_time_horizon_uses_reverse_range_seek(graph):
    versions = [
        graph.put_node_version(Node(f"n{ordinal}"), valid=(0, None))
        for ordinal in range(5)
    ]
    calls = []
    original = graph.store.iter_range_index

    def tracking(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    graph.store.iter_range_index = tracking
    assert graph._temporal_system_horizon(system_time=versions[-1].system_time) == 5
    assert calls == [{"reverse": True}]


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


def test_deferred_temporal_indexes_fail_closed_and_rebuild(graph):
    version = graph.put_node_version(
        Node("alice"), valid=(0, None), index_mode=IndexMaintenanceMode.DEFER
    )

    assert "temporal" in graph.stale_indexes()
    assert graph.get_temporal_commit(version.commit_id) is not None
    with pytest.raises(RuntimeError, match="temporal"):
        graph.get_node_as_of("alice", valid_time=0)

    counts = graph.rebuild_temporal_indexes()

    assert counts["temporal_exact"] == 1
    assert "temporal" not in graph.stale_indexes()
    assert graph.get_node_as_of("alice", valid_time=0).version_id == version.version_id


def test_defer_rebuild_restores_indexes_before_return(graph):
    version = graph.put_node_version(
        Node("alice"), valid=(0, None), index_mode=IndexMaintenanceMode.DEFER_REBUILD
    )

    assert "temporal" not in graph.stale_indexes()
    assert graph.get_node_version(version.version_id).logical_id == "alice"


def test_exact_version_lookup_uses_temporal_index(graph, monkeypatch):
    version = graph.put_node_version(Node("alice"), valid=(0, None))

    monkeypatch.setattr(
        graph,
        "iter_temporal_commits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("history scan")),
    )

    assert graph.get_node_version(version.version_id).logical_id == "alice"


def test_as_of_and_traversal_do_not_scan_commit_history(graph, monkeypatch):
    asserted = graph.put_edge_version(
        Edge("e1", "alice", "acme", {"type": "WORKS_FOR"}), valid=(0, None)
    )
    graph.correct_edge_version(
        Edge("e1", "alice", "other", {"type": "ADVISES"}),
        supersedes_version_id=asserted.version_id,
    )
    monkeypatch.setattr(
        graph,
        "iter_temporal_commits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("history scan")),
    )

    assert graph.get_edge_as_of("e1", valid_time=0).edge.target == "other"
    assert [version.logical_id for version in graph.iter_edges_as_of(
        target="other", direction="in", edge_type="ADVISES", valid_time=0
    )] == ["e1"]


def test_rebuild_deferred_indexes_includes_temporal_family(graph):
    graph.put_node_version(Node("alice"), valid=(0, None), index_mode="defer")

    rebuilt = graph.rebuild_deferred_indexes()

    assert rebuilt["temporal_exact"] == 1
    assert graph.get_node_as_of("alice", valid_time=0) is not None


def test_empty_temporal_database_returns_absence_without_rebuild(graph):
    assert graph.get_node_as_of("missing", valid_time=0) is None
    assert list(graph.iter_node_versions("missing")) == []
    assert graph.stale_indexes() == ()


def test_pre_index_history_is_reported_stale_and_generic_rebuild_migrates(graph):
    version = graph.put_node_version(Node("alice"), valid=(0, None))
    graph.store.metadata.pop(b"temporal:indexes:v1:state")
    graph.store.metadata.pop(b"schema:indexes:stale", None)
    graph.store.indexes.clear()
    graph.store.range_indexes.clear()

    assert graph.stale_indexes() == ("temporal",)
    with pytest.raises(RuntimeError, match="temporal"):
        graph.get_node_as_of("alice", valid_time=0)

    rebuilt = graph.rebuild_deferred_indexes()

    assert rebuilt["temporal_exact"] == 1
    assert graph.get_node_version(version.version_id).logical_id == "alice"
    assert graph.get_node_as_of("alice", valid_time=0).version_id == version.version_id


def test_long_correction_chain_avoids_recursive_commit_validation(graph):
    version = graph.put_node_version(Node("alice", properties={"revision": 0}), valid=(0, None))
    for revision in range(1, 40):
        version = graph.correct_node_version(
            Node("alice", properties={"revision": revision}),
            supersedes_version_id=version.version_id,
        )

    assert graph.get_node_as_of("alice", valid_time=0).node.properties["revision"] == 39
    assert graph.get_temporal_commit(version.commit_id).versions[0].version_id == version.version_id


def test_same_commit_last_eligible_ordinal_wins(graph):
    commit = graph.commit_versions([
        NodeVersionWrite.assertion(Node("alice", properties={"revision": 1}), (0, None)),
        NodeVersionWrite.assertion(Node("alice", properties={"revision": 2}), (0, None)),
    ])

    winner = graph.get_node_as_of("alice", valid_time=0)

    assert winner.commit_id == commit.commit_id
    assert winner.commit_ordinal == 1
    assert winner.node.properties["revision"] == 2


def test_incremental_and_rebuilt_indexes_have_matching_results():
    maintained = GraphDB(_MetadataStore(), JSONSerializer())
    rebuilt = GraphDB(_MetadataStore(), JSONSerializer())
    for graph, mode in ((maintained, "maintain"), (rebuilt, "defer")):
        first = graph.put_edge_version(
            Edge("e1", "alice", "acme", {"type": "WORKS_FOR"}),
            valid=(-10, 20),
            index_mode=mode,
        )
        graph.correct_edge_version(
            Edge("e1", "alice", "other", {"type": "ADVISES"}),
            supersedes_version_id=first.version_id,
            valid=(0, 10),
            index_mode=mode,
        )
    rebuilt.rebuild_temporal_indexes()

    for valid_time in (-10, 0, 10, 19, 20):
        left = maintained.get_edge_as_of("e1", valid_time=valid_time)
        right = rebuilt.get_edge_as_of("e1", valid_time=valid_time)
        assert (None if left is None else left.edge.to_dict()) == (
            None if right is None else right.edge.to_dict()
        )
    assert [v.edge.to_dict() for v in maintained.iter_edges_as_of(
        source="alice", edge_type="ADVISES", valid_time=5
    )] == [v.edge.to_dict() for v in rebuilt.iter_edges_as_of(
        source="alice", edge_type="ADVISES", valid_time=5
    )]


def test_temporal_indexes_and_rebuild_on_available_backends(graph_db):
    asserted = graph_db.put_edge_version(
        Edge("e1", "alice", "acme", {"type": "WORKS_FOR"}), valid=(-10, 20)
    )
    corrected = graph_db.correct_edge_version(
        Edge("e1", "alice", "other", {"type": "ADVISES"}),
        supersedes_version_id=asserted.version_id,
        valid=(0, 10),
        index_mode="defer",
    )
    with pytest.raises(RuntimeError, match="temporal"):
        graph_db.get_edge_as_of("e1", valid_time=5)

    graph_db.rebuild_deferred_indexes()

    assert graph_db.get_edge_as_of("e1", valid_time=-1).version_id == asserted.version_id
    assert graph_db.get_edge_as_of("e1", valid_time=5).version_id == corrected.version_id
    incoming = list(graph_db.iter_edges_as_of(
        target="other", direction="in", edge_type="ADVISES", valid_time=5
    ))
    assert [version.version_id for version in incoming] == [corrected.version_id]
