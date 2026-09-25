from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import MappingProxyType

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore, LevelDBStore
from gestaltdb.serializers import JSONSerializer, MessagePackSerializer, PickleSerializer, ProtobufSerializer, Serializer
from gestaltdb.temporal import TemporalInstant, TemporalInterval
from gestaltdb.versioning import (
    EdgeVersion,
    EdgeVersionWrite,
    NodeVersion,
    NodeVersionWrite,
    TemporalCorruptionError,
    TemporalVersionError,
    VersionOperation,
    canonical_json_bytes,
)


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

    def delete_metadata(self, key):
        self.metadata.pop(key, None)

    def put_index_entry(self, name, parts, value):
        self.indexes.setdefault((name, tuple(parts)), set()).add(value)

    def put_index_entries_bulk(self, entries):
        for name, parts, value in entries:
            self.put_index_entry(name, parts, value)

    def delete_index_entry(self, name, parts, value):
        self.indexes.get((name, tuple(parts)), set()).discard(value)

    def iter_index_prefix(self, name, parts):
        return iter(sorted(self.indexes.get((name, tuple(parts)), set())))

    def put_range_index_entry(self, name, parts, range_value, value):
        self.range_indexes.setdefault((name, tuple(parts)), set()).add((range_value, value))

    def put_range_index_entries_bulk(self, entries):
        for name, parts, range_value, value in entries:
            self.put_range_index_entry(name, parts, range_value, value)

    def delete_range_index_entry(self, name, parts, range_value, value):
        self.range_indexes.get((name, tuple(parts)), set()).discard((range_value, value))

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


class _FailingMetadataStore(_MetadataStore):
    def __init__(self, fail_on_put):
        super().__init__()
        self.fail_on_put = fail_on_put
        self.put_count = 0

    def put_metadata(self, key, value):
        self.put_count += 1
        if self.put_count == self.fail_on_put:
            raise RuntimeError("injected metadata failure")
        super().put_metadata(key, value)


class _BrokenSerializer(Serializer):
    def serialize(self, obj):
        return b"broken"

    def deserialize(self, data):
        raise ValueError("cannot decode")


@pytest.fixture
def temporal_graph():
    return GraphDB(_MetadataStore(), JSONSerializer())


def test_assert_versions_are_append_only_and_do_not_materialize_current_records(temporal_graph):
    node = Node("alice", labels=["Person"], properties={"name": "Alice"})
    edge = Edge("employment-1", "alice", "acme", {"type": "WORKS_FOR"})

    node_version = temporal_graph.put_node_version(
        node, valid=(TemporalInstant.parse("2020-01-01T00:00:00Z"), None)
    )
    edge_version = temporal_graph.put_edge_version(
        edge, valid=TemporalInterval.parse("2021-01-01T00:00:00Z")
    )

    assert isinstance(node_version, NodeVersion)
    assert isinstance(edge_version, EdgeVersion)
    assert node_version.logical_id == "alice"
    assert edge_version.logical_id == "employment-1"
    assert node_version.operation is VersionOperation.ASSERT
    assert edge_version.commit_id == node_version.commit_id + 1
    assert [item.version_id for item in temporal_graph.iter_node_versions("alice")] == [node_version.version_id]
    assert [item.version_id for item in temporal_graph.iter_edge_versions("employment-1")] == [edge_version.version_id]
    assert temporal_graph.store.metadata.get(b"alice") is None


def test_correction_and_retraction_preserve_prior_payloads(temporal_graph):
    original = temporal_graph.put_node_version(
        Node("alice", properties={"name": "Alice"}), valid=(0, None)
    )
    corrected = temporal_graph.correct_node_version(
        Node("alice", properties={"name": "Alicia"}),
        supersedes_version_id=original.version_id,
    )
    retracted = temporal_graph.retract_node_version(
        "alice",
        valid_from="2030-01-01T00:00:00Z",
        supersedes_version_id=corrected.version_id,
        reason="source withdrew assertion",
    )

    assert original.node.properties == {"name": "Alice"}
    assert corrected.node.properties == {"name": "Alicia"}
    assert corrected.valid == original.valid
    assert corrected.operation is VersionOperation.CORRECT
    assert retracted.node is None
    assert retracted.operation is VersionOperation.RETRACT
    assert retracted.reason == "source withdrew assertion"
    assert retracted.valid.start == TemporalInstant.parse("2030-01-01T00:00:00Z")


def test_batch_commit_assigns_one_commit_and_ordered_ordinals(temporal_graph):
    commit = temporal_graph.commit_versions(
        [
            NodeVersionWrite.assertion(Node("alice"), (0, None)),
            EdgeVersionWrite.assertion(Edge("e1", "alice", "acme", {"type": "WORKS_FOR"}), (0, 10)),
        ],
        metadata={"source": "fixture", "tags": ["a", "b"]},
    )

    assert [version.commit_id for version in commit.versions] == [commit.commit_id, commit.commit_id]
    assert [version.commit_ordinal for version in commit.versions] == [0, 1]
    assert commit.metadata == {"source": "fixture", "tags": ("a", "b")}
    assert isinstance(commit.metadata, MappingProxyType)
    with pytest.raises(TypeError):
        commit.metadata["source"] = "changed"


def test_system_time_is_strictly_monotonic_when_clock_repeats(temporal_graph):
    fixed = datetime(2025, 1, 1, tzinfo=timezone.utc)
    temporal_graph._temporal_clock = lambda: fixed

    first = temporal_graph.put_node_version(Node("n1"), valid=(0, None))
    second = temporal_graph.put_node_version(Node("n2"), valid=(0, None))

    assert second.system_time.epoch_microseconds == first.system_time.epoch_microseconds + 1


def test_correction_validates_superseded_kind_and_logical_id(temporal_graph):
    original = temporal_graph.put_node_version(Node("alice"), valid=(0, None))

    with pytest.raises(TemporalVersionError, match="different kind or logical ID"):
        temporal_graph.correct_node_version(
            Node("bob"), supersedes_version_id=original.version_id
        )
    with pytest.raises(TemporalVersionError, match="different kind or logical ID"):
        temporal_graph.correct_edge_version(
            Edge("alice", "a", "b", {"type": "T"}),
            supersedes_version_id=original.version_id,
        )


def test_retraction_requires_interval_or_superseded_version(temporal_graph):
    with pytest.raises(TemporalVersionError, match="validity interval"):
        temporal_graph.retract_node_version("alice")
    with pytest.raises(TemporalVersionError, match="either valid or valid_from"):
        temporal_graph.retract_node_version("alice", valid=(0, 1), valid_from=0)


def test_duplicate_version_ids_and_invalid_metadata_are_rejected(temporal_graph):
    version_id = "3f287de6-62f1-4a3b-961d-54485860849e"
    temporal_graph.put_node_version(Node("n1"), valid=(0, None), version_id=version_id)

    with pytest.raises(TemporalVersionError, match="already exists"):
        temporal_graph.put_node_version(Node("n2"), valid=(0, None), version_id=version_id)
    with pytest.raises(TemporalVersionError, match="JSON-compatible"):
        temporal_graph.put_node_version(Node("n3"), valid=(0, None), metadata={"bad": {1, 2}})
    with pytest.raises(TemporalVersionError, match="at least one"):
        temporal_graph.commit_versions([])


def test_direct_write_descriptors_are_normalized_and_validated(temporal_graph):
    commit = temporal_graph.commit_versions([
        NodeVersionWrite(VersionOperation.ASSERT, "alice", (0, None), node=Node("alice"))
    ])
    assert commit.versions[0].valid == TemporalInterval.from_values(0)

    with pytest.raises(TemporalVersionError, match="reason"):
        temporal_graph.commit_versions([
            NodeVersionWrite(
                VersionOperation.RETRACT,
                "alice",
                TemporalInterval.from_values(0),
                reason={"invalid": True},
            )
        ])


def test_serializer_is_preflighted_before_commit_publication():
    store = _MetadataStore()
    graph = GraphDB(store, _BrokenSerializer())

    with pytest.raises(TemporalVersionError, match="cannot decode"):
        graph.put_node_version(Node("alice"), valid=(0, None))

    assert graph._temporal_sequence() == (0, None)
    assert list(graph.iter_temporal_commits()) == []


def test_stale_or_missing_sequence_never_overwrites_history(temporal_graph):
    first = temporal_graph.put_node_version(Node("alice"), valid=(0, None))
    temporal_graph.store.metadata.pop(b"temporal:v1:sequence")

    with pytest.raises(TemporalCorruptionError, match="overwrite"):
        temporal_graph.put_node_version(Node("bob"), valid=(0, None))

    temporal_graph.store.metadata[b"temporal:v1:sequence"] = b'{"commit_id":1,"system_time_us":0}'
    assert temporal_graph.get_node_version(first.version_id).logical_id == "alice"


@pytest.mark.parametrize("fail_on_put", [2, 3, 4, 5])
def test_marker_last_failures_remain_invisible_and_skip_reserved_commit(fail_on_put):
    store = _FailingMetadataStore(fail_on_put)
    graph = GraphDB(store, JSONSerializer())

    with pytest.raises(RuntimeError, match="injected"):
        graph.put_node_version(Node("failed"), valid=(0, None))

    assert graph.get_temporal_commit(1) is None
    store.fail_on_put = -1
    succeeding = graph.put_node_version(Node("succeeds"), valid=(0, None))
    assert succeeding.commit_id == 2


def test_temporal_orphans_can_be_listed_and_reclaimed():
    store = _FailingMetadataStore(fail_on_put=4)
    graph = GraphDB(store, JSONSerializer())

    with pytest.raises(RuntimeError, match="injected"):
        graph.put_node_version(Node("failed"), valid=(0, None))
    store.fail_on_put = -1

    report = graph.list_temporal_orphans()
    assert report.allocation_horizon == 1
    assert [(item.commit_id, item.kind, item.present_record_count) for item in report.artifacts] == [
        (1, "incomplete_commit", 1)
    ]

    result = graph.reclaim_temporal_orphans(through_commit=1)
    assert result.reclaimed_commit_ids == (1,)
    assert result.indexes_rebuilt
    assert graph.store.get_metadata(graph._temporal_commit_key(1)) is None
    assert graph.store.get_metadata(graph._temporal_record_key(1, 0)) is None
    assert graph._temporal_sequence()[0] == 1

    succeeding = graph.put_node_version(Node("succeeds"), valid=(0, None))
    assert succeeding.commit_id == 2
    assert [commit.commit_id for commit in graph.iter_temporal_commits()] == [2]


def test_temporal_orphan_listing_distinguishes_empty_reservation_gaps(temporal_graph):
    temporal_graph.put_node_version(Node("visible"), valid=(0, None))
    temporal_graph.store.metadata[b"temporal:v1:sequence"] = canonical_json_bytes({
        "commit_id": 2,
        "system_time_us": 1,
    })

    report = temporal_graph.list_temporal_orphans()
    assert [(item.commit_id, item.kind) for item in report.artifacts] == [
        (2, "reservation_gap")
    ]
    result = temporal_graph.reclaim_temporal_orphans(through_commit=2)
    assert result.reclaimed_commit_ids == ()
    assert result.reservation_gap_ids == (2,)
    assert not result.indexes_rebuilt


def test_incomplete_commit_is_invisible_and_marker_corruption_is_detected(temporal_graph):
    version = temporal_graph.put_node_version(Node("alice"), valid=(0, None))
    visible_key = temporal_graph._temporal_visible_key(version.commit_id)
    marker = temporal_graph.store.metadata.pop(visible_key)

    assert temporal_graph.get_temporal_commit(version.commit_id) is None
    with pytest.raises(TemporalCorruptionError, match="checksum"):
        temporal_graph.get_node_version(version.version_id)

    temporal_graph.store.metadata[visible_key] = b"not-the-descriptor-hash"
    with pytest.raises(TemporalCorruptionError, match="marker"):
        temporal_graph.get_temporal_commit(version.commit_id)
    temporal_graph.store.metadata[visible_key] = marker
    assert temporal_graph.get_temporal_commit(version.commit_id).versions[0].version_id == version.version_id


def test_marker_backed_invalid_descriptor_shape_raises_corruption(temporal_graph):
    version = temporal_graph.put_node_version(Node("alice"), valid=(0, None))
    descriptor_key = temporal_graph._temporal_commit_key(version.commit_id)
    marker_key = temporal_graph._temporal_visible_key(version.commit_id)
    invalid_descriptor = b"[]"
    temporal_graph.store.metadata[descriptor_key] = invalid_descriptor
    temporal_graph.store.metadata[marker_key] = hashlib.sha256(invalid_descriptor).digest()

    with pytest.raises(TemporalCorruptionError, match="object"):
        temporal_graph.get_temporal_commit(version.commit_id)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda descriptor: descriptor.update(version_ids=[], record_digests=[]), "no versions"),
        (lambda descriptor: descriptor.update(system_time_us="0"), "metadata"),
    ],
)
def test_marker_backed_semantically_invalid_descriptor_raises_corruption(
    temporal_graph, mutation, match
):
    version = temporal_graph.put_node_version(Node("alice"), valid=(0, None))
    descriptor_key = temporal_graph._temporal_commit_key(version.commit_id)
    marker_key = temporal_graph._temporal_visible_key(version.commit_id)
    descriptor = json.loads(temporal_graph.store.metadata[descriptor_key])
    mutation(descriptor)
    invalid_descriptor = json.dumps(
        descriptor, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporal_graph.store.metadata[descriptor_key] = invalid_descriptor
    temporal_graph.store.metadata[marker_key] = hashlib.sha256(invalid_descriptor).digest()

    with pytest.raises(TemporalCorruptionError, match=match):
        temporal_graph.get_temporal_commit(version.commit_id)


def test_through_commit_filters_history(temporal_graph):
    first = temporal_graph.put_node_version(Node("alice"), valid=(0, None))
    temporal_graph.correct_node_version(
        Node("alice", properties={"revision": 2}), supersedes_version_id=first.version_id
    )

    versions = list(temporal_graph.iter_node_versions("alice", through_commit=first.commit_id))

    assert [version.version_id for version in versions] == [first.version_id]


def test_temporal_versions_round_trip_backend_storage(graph_db):
    first = graph_db.put_node_version(Node("alice"), valid=(0, None))
    fetched = graph_db.get_node_version(first.version_id)

    assert fetched.version_id == first.version_id
    assert fetched.node.get_id == first.node.get_id
    assert fetched.node is not first.node


def test_temporal_versions_persist_across_leveldb_reopen(tmp_path):
    pytest.importorskip("plyvel")
    path = str(tmp_path / "leveldb")
    graph = GraphDB(LevelDBStore(path=path), JSONSerializer())
    first = graph.put_node_version(
        Node("alice", properties={"revision": 1}),
        valid=(0, None),
        metadata={"source": "reopen-test"},
    )
    graph.close()

    reopened = GraphDB(LevelDBStore(path=path), JSONSerializer())
    try:
        fetched = reopened.get_node_version(first.version_id)
        second = reopened.put_node_version(Node("bob"), valid=(0, None))
        assert fetched.node.properties == {"revision": 1}
        assert reopened.get_temporal_commit(first.commit_id).metadata == {"source": "reopen-test"}
        assert second.commit_id == first.commit_id + 1
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "serializer",
    [JSONSerializer(), PickleSerializer(), MessagePackSerializer(), ProtobufSerializer()],
    ids=["json", "pickle", "messagepack", "protobuf"],
)
def test_temporal_versions_round_trip_supported_serializers(serializer):
    graph = GraphDB(_MetadataStore(), serializer)
    version = graph.put_node_version(
        Node("alice", labels=["Person"], properties={"nested": {"count": 2}}),
        valid=(-1, None),
    )

    fetched = graph.get_node_version(version.version_id)
    assert fetched.node.labels == ("Person",)
    assert fetched.node.properties == {"nested": {"count": 2}}
    fetched.node.properties["nested"]["count"] = 99
    assert graph.get_node_version(version.version_id).node.properties["nested"]["count"] == 2


def test_temporal_versions_participate_in_supported_outer_transactions(lmdb_graph_db):
    rolled_back = None
    with pytest.raises(RuntimeError):
        with lmdb_graph_db.transaction() as transaction:
            rolled_back = transaction.put_node_version(Node("rolled-back"), valid=(0, None))
            raise RuntimeError("rollback")

    assert list(lmdb_graph_db.iter_node_versions("rolled-back")) == []

    with lmdb_graph_db.transaction() as transaction:
        committed = transaction.put_node_version(Node("committed"), valid=(0, None))

    assert committed.commit_id > rolled_back.commit_id
    assert lmdb_graph_db.get_node_version(committed.version_id) is not None


def test_all_temporal_ids_from_rolled_back_outer_transaction_are_consumed(lmdb_graph_db):
    rolled_back = []
    with pytest.raises(RuntimeError):
        with lmdb_graph_db.transaction() as transaction:
            rolled_back.append(transaction.put_node_version(Node("first"), valid=(0, None)))
            rolled_back.append(transaction.put_node_version(Node("second"), valid=(0, None)))
            raise RuntimeError("rollback")

    committed = lmdb_graph_db.put_node_version(Node("committed"), valid=(0, None))

    assert committed.commit_id > max(version.commit_id for version in rolled_back)
    assert list(lmdb_graph_db.iter_node_versions("first")) == []
    assert list(lmdb_graph_db.iter_node_versions("second")) == []


def test_rolled_back_temporal_id_reservation_survives_reopen(tmp_path):
    pytest.importorskip("lmdb")
    path = tmp_path / "lmdb-reservation"
    graph = GraphDB(LMDBStore(path=str(path)), JSONSerializer())
    rolled_back_id = None
    try:
        with pytest.raises(RuntimeError):
            with graph.transaction() as transaction:
                rolled_back_id = transaction.put_node_version(
                    Node("rolled-back"), valid=(0, None)
                ).commit_id
                raise RuntimeError("rollback")
    finally:
        graph.close()

    reopened = GraphDB(LMDBStore(path=str(path)), JSONSerializer())
    try:
        committed = reopened.put_node_version(Node("committed"), valid=(0, None))
        assert committed.commit_id > rolled_back_id
    finally:
        reopened.close()


def test_temporal_interval_inputs_still_require_non_empty_half_open_ranges(temporal_graph):
    with pytest.raises(ValueError, match="greater than start"):
        temporal_graph.put_node_version(Node("alice"), valid=(10, 10))

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    version = temporal_graph.put_node_version(Node("alice"), valid=(start, end))
    assert version.valid.contains(start)
    assert not version.valid.contains(end)
