"""PropEnh-09 (#122): snapshot consistency metadata, appends and compaction.

Backend-free coverage with a versioned fake graph: consistency metadata is
stamped at build, staleness is detected on source movement, appends extend
maps append-only with stable compact IDs, and compaction rebuilds, verifies
and optionally prunes against the source.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from gestaltdb.graphdb import Edge, Node
from gestaltdb.sampling import SamplerSnapshot


class _FakeProvenance:
    def __init__(self, payload):
        self._payload = dict(payload)

    def to_dict(self):
        return dict(self._payload)


class _FakeReadView:
    def __init__(self, provenance):
        self.provenance = provenance


class _FakeVersionedStore:
    def __init__(self, nodes, edges):
        self.nodes = {node.get_id_bytes: node for node in nodes}
        self.edges = {edge.get_id_bytes: edge for edge in edges}

    def get_node_keys_generator(self):
        return iter(self.nodes.keys())

    def get_edge_keys_generator(self):
        return iter(self.edges.keys())


class _FakeVersionedGraph:
    """Graph-like double with an observable source-version token."""

    def __init__(self):
        self.store = _FakeVersionedStore(
            [
                Node(node_id="drug-1", properties={"kind": "drug"}),
                Node(node_id="drug-2", properties={"kind": "drug"}),
                Node(node_id="protein-1", properties={"kind": "protein"}),
            ],
            [
                Edge(edge_id="d1-p1", source="drug-1", target="protein-1",
                     properties={"type": "drug-to-protein"}),
            ],
        )
        self._sequence = 7

    def _version(self):
        return {
            "kind": "current-read-view",
            "database_id": "db-1",
            "snapshot_sequence": self._sequence,
            "state_sha256": f"{self._sequence:064x}",
            "token": f"token-{self._sequence}",
        }

    @contextmanager
    def current_read_view(self):
        yield _FakeReadView(_FakeProvenance(self._version()))

    def bump_version(self):
        self._sequence += 1

    def add_edge(self, edge):
        self.store.edges[edge.get_id_bytes] = edge
        self.bump_version()

    def remove_edge(self, edge_id):
        for key, edge in list(self.store.edges.items()):
            if edge.get_id == edge_id:
                del self.store.edges[key]
                self.bump_version()
                return
        raise KeyError(edge_id)

    def get_node(self, node_key):
        return self.store.nodes.get(node_key)

    def get_edge(self, edge_key):
        return self.store.edges.get(edge_key)

    def key_to_string(self, key):
        return key.decode("utf-8") if isinstance(key, bytes) else key


def _edge_keys(graph):
    return list(graph.store.get_edge_keys_generator())


def test_build_stamps_consistency_metadata(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")

    assert snapshot.metadata["snapshot_id"]
    assert snapshot.metadata["source_version"] == graph._version()
    assert snapshot.metadata["schema_hash"]
    assert snapshot.metadata["append_generation"] == 0
    assert snapshot.metadata["compactions"] == 0
    assert snapshot.metadata["filtered"] is False

    reloaded = SamplerSnapshot.load(tmp_path / "snap")
    assert reloaded.metadata["snapshot_id"] == snapshot.metadata["snapshot_id"]
    assert not snapshot.is_stale(graph)
    assert snapshot.check_fresh(graph) == graph._version()


def test_source_movement_reports_stale_and_fails_closed(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")
    assert not snapshot.is_stale(graph)

    graph.bump_version()
    assert snapshot.is_stale(graph)
    with pytest.raises(ValueError, match="moved past the snapshot version"):
        snapshot.check_fresh(graph)


def test_unversioned_snapshots_are_always_stale(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")
    snapshot.metadata["source_version"] = None
    assert snapshot.is_stale(graph)
    with pytest.raises(ValueError, match="no verifiable source version"):
        snapshot.check_fresh(graph)

    class _Unobservable:
        store = _FakeVersionedStore([], [])

        def get_node(self, key):
            return None

        def get_edge(self, key):
            return None

        def key_to_string(self, key):
            return key

    assert snapshot.is_stale(_Unobservable())


def test_append_extends_maps_without_moving_existing_ids(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")
    old_node = snapshot.node_int("drug-1")
    old_edge_count = snapshot.num_edges

    graph.store.nodes[b"protein-2"] = Node(node_id="protein-2", properties={"kind": "protein"})
    graph.add_edge(Edge(edge_id="d1-p2", source="drug-1", target="protein-2",
                        properties={"type": "drug-to-protein"}))
    graph.add_edge(Edge(edge_id="p1-d2", source="protein-1", target="drug-2",
                        properties={"type": "protein-to-drug"}))
    new_keys = [key for key in _edge_keys(graph)
                if graph.get_edge(key).get_id in ("d1-p2", "p1-d2")]
    assert len(new_keys) == 2

    summary = snapshot.append_from_graph(graph, new_keys)
    assert summary == {
        "appended_edges": 2,
        "appended_nodes": 1,
        "appended_relations": 1,
        "append_generation": 1,
        "snapshot_id": snapshot.metadata["snapshot_id"],
    }
    assert snapshot.node_int("drug-1") == old_node
    assert snapshot.node_int("protein-2") == snapshot.num_nodes - 1
    assert snapshot.num_edges == old_edge_count + 2
    assert snapshot.metadata["append_generation"] == 1
    assert snapshot.metadata["source_version"] == graph._version()
    assert not snapshot.is_stale(graph)

    reloaded = SamplerSnapshot.load(tmp_path / "snap")
    assert reloaded.num_edges == snapshot.num_edges
    assert reloaded.metadata["snapshot_id"] == snapshot.metadata["snapshot_id"]
    triples = {tuple(row) for row in reloaded.positive_triples.tolist()}
    assert (reloaded.node_int("protein-1"),
            reloaded.relation_int("protein-to-drug"),
            reloaded.node_int("drug-2")) in triples


def test_append_rejects_duplicates_unknown_keys_and_schema_drift(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")

    with pytest.raises(ValueError, match="duplicate external edge ID"):
        snapshot.append_from_graph(graph, _edge_keys(graph))
    with pytest.raises(ValueError, match="does not resolve"):
        snapshot.append_from_graph(graph, [b"missing-edge"])
    with pytest.raises(ValueError, match="non-empty"):
        snapshot.append_from_graph(graph, [])
    with pytest.raises(ValueError, match="edge_type_property"):
        snapshot.append_from_graph(graph, _edge_keys(graph), edge_type_property="")
    with pytest.raises(ValueError, match="does not match the snapshot schema hash"):
        snapshot.append_from_graph(graph, _edge_keys(graph), edge_type_property="kind")

    arrays = SamplerSnapshot.from_arrays(
        tmp_path / "raw", external_node_ids=["a"], node_type_ids=[-1],
        external_edge_ids=["e"], external_relation_ids=["r"],
        src_int=[0], dst_int=[0], rel_int=[0])
    with pytest.raises(ValueError, match="lacks recorded build interpretation"):
        arrays.append_from_graph(graph, _edge_keys(graph))


def test_append_and_compact_reject_temporal_and_legacy(tmp_path):
    snapshot = SamplerSnapshot.from_edge_arrays(
        tmp_path / "array", external_node_ids=["a", "b"], external_edge_ids=["e"],
        external_relation_ids=["r"], src_int=[0], dst_int=[1], rel_int=[0])
    snapshot.metadata["temporal"] = True
    with pytest.raises(ValueError, match="do not support append"):
        snapshot.append_from_graph(_FakeVersionedGraph(), [b"e"])
    with pytest.raises(ValueError, match="do not support compact"):
        snapshot.compact()


def test_compact_rebuilds_verifies_and_prunes_against_source(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(graph, tmp_path / "snap")

    summary = snapshot.compact()
    assert summary["pruned_edges"] == 0
    assert summary["compactions"] == 1
    assert snapshot.metadata["compactions"] == 1
    assert snapshot.metadata["snapshot_id"] == summary["snapshot_id"]
    assert SamplerSnapshot.load(tmp_path / "snap").num_edges == 1

    graph.add_edge(Edge(edge_id="d2-p1", source="drug-2", target="protein-1",
                        properties={"type": "drug-to-protein"}))
    snapshot.append_from_graph(graph, [key for key in _edge_keys(graph)
                                       if graph.get_edge(key).get_id == "d2-p1"])
    assert snapshot.num_edges == 2

    graph.remove_edge("d2-p1")
    pruned = snapshot.compact(graph)
    assert pruned == {"pruned_edges": 1, "edge_count": 1, "compactions": 2,
                      "snapshot_id": snapshot.metadata["snapshot_id"]}
    survivor = snapshot.node_int("drug-1")
    assert survivor == 0  # node IDs stay append-ordered, never reindexed
    assert SamplerSnapshot.load(tmp_path / "snap").num_edges == 1
    assert not snapshot.is_stale(graph)


def test_compact_rejects_prune_for_filtered_snapshots(tmp_path):
    graph = _FakeVersionedGraph()
    snapshot = SamplerSnapshot.build(
        graph, tmp_path / "filtered",
        edge_filter=lambda edge: edge.get_id != "d1-p1")
    assert snapshot.metadata["filtered"] is True
    with pytest.raises(ValueError, match="cannot prune filtered snapshots"):
        snapshot.compact(graph)
    # Canonical rebuild without a graph is still allowed.
    assert snapshot.compact()["compactions"] == 1
