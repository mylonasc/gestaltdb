import pytest

from gestaltdb.kvstores import KVStore, _pack_long_int, _typed_adjacency_prefix, _unpack_long_int


def test_integer_pack_helpers_round_trip():
    assert _unpack_long_int(_pack_long_int(42)) == 42


def test_typed_adjacency_prefix_uses_typed_key_layout():
    assert _typed_adjacency_prefix("out", b"drug-1", "rel") == b"out\x1fdrug-1\x1frel\x1f"


def test_kvstore_abstract_methods_raise_not_implemented():
    store = KVStore()

    calls = [
        lambda: store.put(b"k", b"v"),
        lambda: store.get(b"k"),
        lambda: store.delete(b"k"),
        lambda: list(store.range_iter(b"a", b"z")),
        store.close,
        lambda: store.put_metadata(b"k", b"v"),
        lambda: store.get_metadata(b"k"),
        lambda: store.delete_metadata(b"k"),
        lambda: store.put_node(b"n", b"v"),
        lambda: store.get_node(b"n"),
        lambda: store.delete_node(b"n"),
        lambda: store.put_edge(b"e", b"v"),
        lambda: store.get_edge(b"e"),
        lambda: store.delete_edge(b"e"),
        lambda: store.put_nodes_bulk({b"n": b"v"}),
        lambda: store.get_nodes_bulk([b"n"]),
        lambda: store.put_edges_bulk({b"e": b"v"}),
        lambda: store.get_edges_bulk([b"e"]),
        lambda: store.put_typed_adjacency(b"n1", b"n2", "rel", b"e"),
        lambda: store.delete_typed_adjacency(b"n1", b"n2", "rel", b"e"),
        lambda: list(store.iter_typed_adjacency(b"n1", "rel")),
        lambda: store.put_index_entry("idx", [b"k"], b"v"),
        lambda: store.delete_index_entry("idx", [b"k"], b"v"),
        lambda: list(store.iter_index_prefix("idx", [b"k"])),
    ]

    for call in calls:
        with pytest.raises(NotImplementedError):
            call()


def test_kvstore_default_bulk_helpers_delegate_to_single_entry_methods():
    class RecordingStore(KVStore):
        def __init__(self):
            self.typed_records = []
            self.index_records = []

        def put_typed_adjacency(self, source_id, target_id, edge_type, edge_id):
            self.typed_records.append((source_id, target_id, edge_type, edge_id))

        def put_index_entry(self, index_name, key_parts, value):
            self.index_records.append((index_name, key_parts, value))

    store = RecordingStore()

    store.put_typed_adjacency_bulk([(b"n1", b"n2", "rel", b"e1")])
    store.put_index_entries_bulk([("node_label", [b"Drug"], b"drug-1")])

    assert store.typed_records == [(b"n1", b"n2", "rel", b"e1")]
    assert store.index_records == [("node_label", [b"Drug"], b"drug-1")]


def test_store_sorted_index_prefix_iteration(graph_db):
    graph_db.store.put_index_entries_bulk([
        ("node_label", [b"Drug"], b"drug-1"),
        ("node_label", [b"Drug"], b"drug-2"),
        ("node_label", [b"Protein"], b"protein-1"),
    ])

    assert set(graph_db.store.iter_index_prefix("node_label", [b"Drug"])) == {b"drug-1", b"drug-2"}


def test_store_sorted_index_delete_entry(graph_db):
    graph_db.store.put_index_entry("node_label", [b"Drug"], b"drug-1")
    graph_db.store.delete_index_entry("node_label", [b"Drug"], b"drug-1")

    assert list(graph_db.store.iter_index_prefix("node_label", [b"Drug"])) == []


def test_store_metadata_round_trip(graph_db):
    graph_db.store.put_metadata(b"schema", b"value")
    assert graph_db.store.get_metadata(b"schema") == b"value"

    graph_db.store.delete_metadata(b"schema")
    assert graph_db.store.get_metadata(b"schema") is None
