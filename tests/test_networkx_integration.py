"""Optional adapters exercise public writes across backends and serializers."""
from copy import deepcopy
import gzip
import io
import bz2

import pytest

nx = pytest.importorskip("networkx")

from .conftest import BACKEND_PARAMS, SERIALIZER_PARAMS, blocked_import
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.integrations.networkx import (
    ImportWriteError, from_networkx, load_gexf, to_networkx,
)


@pytest.fixture(params=BACKEND_PARAMS, ids=lambda item: item[0])
def adapter_db(request, tmp_path):
    _, backend = request.param
    def create(serializer):
        return GraphDB(backend(path=str(tmp_path / "db")), serializer)
    return create


@pytest.mark.parametrize("serializer", SERIALIZER_PARAMS)
def test_roundtrip(adapter_db, serializer):
    db = adapter_db(serializer)
    try:
        graph = nx.MultiDiGraph(name="example")
        graph.add_node(1, labels=["Person"], name="Alice", nested={"x": [1, True, None]})
        graph.add_node("1", name="Bob")
        graph.add_node("isolated")
        graph.add_edge(1, "1", key=("edge", 2), id="e1", relation="KNOWS", weight=2.5)
        graph.add_edge(1, "1", key=4, id="e2", relation="KNOWS")
        graph.add_edge(1, 1, key=0, id="loop")
        original = deepcopy(graph)
        db.create_node_property_index("name")
        db.create_edge_property_index("weight")
        result = from_networkx(graph, db, node_labels_attribute="labels", edge_type_attribute="relation", batch_size=1)
        assert (result.imported_nodes, result.imported_edges) == (3, 3)
        assert result.node_ids[1] != result.node_ids["1"]
        assert result.graph_attributes == {"name": "example"}
        assert db.get_node(result.node_ids[1].encode()).labels == ("Person",)
        assert len(list(db.nodes_by_property("name", "Alice"))) == 1
        assert len(list(db.edges_by_property("weight", 2.5))) == 1
        output = to_networkx(db, graph_attributes=result.graph_attributes)
        assert isinstance(output, nx.MultiDiGraph)
        assert set(output.nodes) == set(graph.nodes)
        assert set(output.edges) == set(graph.edges)
        assert output.edges[1, "1", ("edge", 2)]["type"] == "KNOWS"
        assert output.nodes[1]["nested"] == graph.nodes[1]["nested"]
        assert output.edges[1, "1", ("edge", 2)]["__gestaltdb_networkx__"]["id"] == "e1"
        assert nx.utils.graphs_equal(graph, original)
        assert not db.get_node(b"1").labels
        with pytest.raises(ValueError, match="collapse"):
            to_networkx(db, multigraph=False)
    finally:
        db.close()


def test_reimport_export(lmdb_graph_db, tmp_path):
    from gestaltdb.kvstores import LMDBStore
    from gestaltdb.serializers import JSONSerializer
    source = nx.MultiDiGraph()
    source.add_node("a", native=["A"])
    source.add_edge("a", "b", key=42, id="edge")
    from_networkx(source, lmdb_graph_db, node_labels_attribute="native")
    other = GraphDB(LMDBStore(str(tmp_path / "other")), JSONSerializer())
    try:
        from_networkx(to_networkx(lmdb_graph_db), other)
        assert other.get_node(b"a").labels == ("A",)
        assert other.get_edge(b"edge").source == "a"
        assert set(to_networkx(other).edges) == set(source.edges)
    finally:
        other.close()


@pytest.mark.parametrize("factory", [nx.Graph, nx.MultiGraph])
def test_undirected(factory, lmdb_graph_db):
    graph = factory()
    graph.add_edge("a", "b")
    graph.add_edge("a", "a")
    with pytest.raises(ValueError, match="Undirected"):
        from_networkx(graph, lmdb_graph_db)
    result = from_networkx(graph, lmdb_graph_db, undirected="bidirectional")
    assert result.imported_edges == 3
    assert result.direction_conversion == "bidirectional"
    assert set(to_networkx(lmdb_graph_db).edges()) == {("a", "b"), ("b", "a"), ("a", "a")}


@pytest.mark.parametrize("case", ["mapped", "existing_node", "existing_edge", "edge_id", "reserved", "reserved_null", "object", "tuple", "key", "relation"])
def test_preflight_rejects(case, lmdb_graph_db):
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", id="e")
    options = {}
    if case == "mapped":
        options["node_id"] = lambda _: "same"
    elif case == "existing_node":
        lmdb_graph_db.put_node(Node("a"))
    elif case == "existing_edge":
        lmdb_graph_db.put_edge(Edge("e", "old", "old"))
    elif case == "edge_id":
        graph.add_edge("a", "b", id="e")
    elif case == "reserved":
        graph.nodes["a"]["__gestaltdb_networkx__"] = "user data"
    elif case == "reserved_null":
        graph.nodes["a"]["__gestaltdb_networkx__"] = None
    elif case == "object":
        graph.nodes["a"]["obj"] = object()
    elif case == "tuple":
        graph.nodes["a"]["tuple"] = (1, 2)
    elif case == "key":
        graph.nodes["a"][1] = "non-string key"
    elif case == "relation":
        graph.edges["a", "b", 0].update(type="X", relation="Y")
        options["edge_type_attribute"] = "relation"
    with pytest.raises((ValueError, TypeError)):
        from_networkx(graph, lmdb_graph_db, **options)
    assert lmdb_graph_db.get_node(b"b") is None


def test_custom_keys_and_transform(lmdb_graph_db):
    key = object()
    graph = nx.DiGraph()
    graph.add_node(key, values=(1, 2))
    result = from_networkx(graph, lmdb_graph_db, node_id=lambda _: "mapped", attribute_transform=lambda kind, attrs: {
        name: list(value) if isinstance(value, tuple) else value for name, value in attrs.items()
    })
    assert result.warnings
    assert to_networkx(lmdb_graph_db).nodes["mapped"]["values"] == [1, 2]
    assert graph.nodes[key]["values"] == (1, 2)


def test_selections(lmdb_graph_db):
    graph = nx.DiGraph()
    graph.add_edge("a", "b", id="e")
    graph.add_node("c")
    from_networkx(graph, lmdb_graph_db)
    assert set(to_networkx(lmdb_graph_db, node_ids=["a", "c"])) == {"a", "c"}
    assert set(to_networkx(lmdb_graph_db, edge_ids=["e"])) == {"a", "b"}
    assert isinstance(to_networkx(lmdb_graph_db, multigraph=False), nx.DiGraph)
    for kwargs in ({"node_ids": ["missing"]}, {"edge_ids": ["missing"]}, {"node_ids": ["a"], "edge_ids": ["e"]}):
        with pytest.raises(ValueError):
            to_networkx(lmdb_graph_db, **kwargs)


def test_write_failure(lmdb_graph_db, monkeypatch):
    graph = nx.DiGraph()
    graph.add_edge("a", "b")
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(lmdb_graph_db, "put_edges_bulk", fail)
    with pytest.raises(ImportWriteError) as exc:
        from_networkx(graph, lmdb_graph_db, batch_size=1)
    assert exc.value.result.imported_nodes == 2
    assert exc.value.result.imported_edges == 0
    assert exc.value.phase == "edges"
    assert isinstance(exc.value.__cause__, OSError)


def test_missing_dependency(lmdb_graph_db):
    with blocked_import("networkx"):
        with pytest.raises(ImportError, match=r"gestaltdb\[networkx\]"):
            to_networkx(lmdb_graph_db)
    with blocked_import("defusedxml"):
        with pytest.raises(ImportError, match=r"gestaltdb\[networkx\]"):
            load_gexf(io.BytesIO(b""), lmdb_graph_db)


def gexf(version="1.2draft", graph_attrs='', body=None):
    if body is None:
        body = '''<attributes class="node"><attribute id="0" title="score" type="integer"><default>7</default></attribute></attributes>
        <nodes><node id="a" label="Alice"/><node id="b" label="Bob"/><node id="isolated"/></nodes>
        <edges><edge id="e1" source="a" target="b" weight="2.5"/><edge id="e2" source="a" target="b"/></edges>'''
    return f'<gexf xmlns="http://www.gexf.net/{version}" version="{version}"><graph defaultedgetype="directed" {graph_attrs}>{body}</graph></gexf>'.encode()


@pytest.mark.parametrize("version", ["1.1draft", "1.2draft"])
def test_gexf_versions(version, lmdb_graph_db):
    result = load_gexf(io.BytesIO(gexf(version)), lmdb_graph_db, label_property="name")
    assert (result.imported_nodes, result.imported_edges) == (3, 2)
    assert lmdb_graph_db.get_node(b"a").properties["name"] == "Alice"
    assert lmdb_graph_db.get_node(b"a").properties["score"] == 7
    assert not lmdb_graph_db.get_node(b"a").labels
    assert lmdb_graph_db.get_edge(b"e1").properties["weight"] == 2.5


def test_gexf_compressed_viz(lmdb_graph_db, tmp_path):
    graph = nx.MultiDiGraph()
    graph.add_node("a", viz={"color": {"r": 255, "g": 0, "b": 0, "a": 1.0}, "size": 8.0})
    path = tmp_path / "graph.gexf.gz"
    nx.write_gexf(graph, path)
    load_gexf(path, lmdb_graph_db)
    assert lmdb_graph_db.get_node(b"a").properties["viz"]["size"] == 8.0


@pytest.mark.parametrize("data", [
    gexf(graph_attrs='mode="dynamic"'),
    gexf(body='<nodes><node id="a" start="1"/></nodes>'),
    gexf(body='<nodes><node id="a"><spells/></node></nodes>'),
    gexf(body='<nodes><node id="a"/><node id="a"/></nodes>'),
    gexf(body='<nodes><node id="a"/></nodes><edges><edge source="a" target="b"/></edges>'),
    gexf(body='<nodes><node id="a"/></nodes><edges><edge source="a" target="a" type="undirected"/></edges>'),
    gexf(body='<nodes><node id="a"/></nodes><edges><edge id="e" source="a" target="a"/><edge id="e" source="a" target="a"/></edges>'),
    gexf(body='<nodes><node id="a"><nodes><node id="b"/></nodes></node></nodes>'),
    gexf("1.3"),
    b'<!DOCTYPE gexf [<!ENTITY x "expanded">]>' + gexf(),
    b'<!DOCTYPE gexf SYSTEM "file:///etc/passwd">' + gexf(),
    b'<broken',
])
def test_gexf_rejects_before_writes(data, lmdb_graph_db):
    with pytest.raises(Exception):
        load_gexf(io.BytesIO(data), lmdb_graph_db)
    assert list(lmdb_graph_db.get_node_keys_generator()) == []


def test_gexf_limits_and_ownership(lmdb_graph_db, tmp_path):
    stream = io.BytesIO(gexf())
    with pytest.raises(ValueError, match="max_bytes"):
        load_gexf(stream, lmdb_graph_db, max_bytes=20)
    assert not stream.closed
    path = tmp_path / "bomb.gz"
    path.write_bytes(gzip.compress(b" " * 10000))
    with pytest.raises(ValueError, match="max_bytes"):
        load_gexf(path, lmdb_graph_db, max_bytes=100)
    with pytest.raises(TypeError, match="binary"):
        load_gexf(io.StringIO("xml"), lmdb_graph_db)


@pytest.mark.parametrize("field", ["label", "property", "type", "adjacency"])
def test_index_limits_preflight(field, lmdb_graph_db):
    graph = nx.DiGraph()
    graph.add_edge("a", "b", id="e")
    options = {}
    if field == "label":
        graph.nodes["a"]["labels"] = ["L" * 600]
        options["node_labels_attribute"] = "labels"
    elif field == "property":
        lmdb_graph_db.create_node_property_index("name")
        graph.nodes["a"]["name"] = "N" * 600
    elif field == "type":
        graph.edges["a", "b"]["type"] = "T" * 600
    else:
        graph = nx.DiGraph()
        graph.add_edge("a" * 250, "b", id="e" * 250, type="T" * 20)
    with pytest.raises(ValueError, match="511-byte"):
        from_networkx(graph, lmdb_graph_db, **options)
    assert list(lmdb_graph_db.get_node_keys_generator()) == []


def test_serializer_preflight(lmdb_graph_db, monkeypatch):
    source = nx.DiGraph()
    source.add_edge("a", "b")
    def reject(*args):
        raise TypeError("unsupported by serializer")
    monkeypatch.setattr(lmdb_graph_db.entity_serializer, "serialize", reject)
    with pytest.raises(TypeError, match="serializer"):
        from_networkx(source, lmdb_graph_db)
    assert list(lmdb_graph_db.get_node_keys_generator()) == []


def test_gexf_bz2_metadata_shape(lmdb_graph_db, tmp_path):
    data = gexf(body='''<nodes><node id="a" label="Alice">
      <viz:shape xmlns:viz="http://www.gexf.net/1.2draft/viz" value="disc"/>
      </node></nodes>''').replace(b'<graph ', b'<meta lastmodifieddate="2026-10-29"><creator>Test</creator></meta><graph ')
    path = tmp_path / "graph.gexf.bz2"
    path.write_bytes(bz2.compress(data))
    result = load_gexf(path, lmdb_graph_db)
    assert result.graph_attributes["gexf_meta"]["elements"][0]["text"] == "Test"
    assert lmdb_graph_db.get_node(b"a").properties["viz"]["shape"] == "disc"


@pytest.mark.parametrize("body", [
    '<nodes><node id="a" custom="lost"/></nodes>',
    '<nodes><node id="a"/></nodes><custom/>',
    '<attributes class="node"><attribute id="x" title="label" type="string"/></attributes>',
    '<attributes class="node"><attribute id="x" title="x" type="string"/><attribute id="y" title="x" type="string"/></attributes>',
    '<nodes><node id="a"><attvalues><attvalue for="x" value="1"/><attvalue for="x" value="2"/></attvalues></node></nodes>',
    '''<nodes><node id="a"/></nodes><edges><edge source="a" target="a">
    <viz:color xmlns:viz="http://www.gexf.net/1.2draft/viz" r="1" g="2" b="3"/>
    </edge></edges>''',
    '''<attributes class="edge"><attribute id="k" title="networkx_key" type="integer"/></attributes>
    <nodes><node id="a"/></nodes><edges>
    <edge id="e1" source="a" target="a"><attvalues><attvalue for="k" value="1"/></attvalues></edge>
    <edge id="e2" source="a" target="a"><attvalues><attvalue for="k" value="1"/></attvalues></edge></edges>''',
])
def test_gexf_rejects_silent_reader_loss(body, lmdb_graph_db):
    with pytest.raises(ValueError):
        load_gexf(io.BytesIO(gexf(body=body)), lmdb_graph_db)
    assert list(lmdb_graph_db.get_node_keys_generator()) == []


def test_dependency_import_isolation():
    import subprocess
    import sys
    script = '''
import builtins, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in ('networkx', 'defusedxml'):
        raise ImportError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
import gestaltdb.integrations.networkx
assert 'networkx' not in sys.modules and 'defusedxml' not in sys.modules
'''
    subprocess.run([sys.executable, "-c", script], check=True)


def test_export_copies_and_native_records(lmdb_graph_db):
    lmdb_graph_db.put_node(Node("a", {"values": [1]}, labels=["A"]))
    lmdb_graph_db.put_node(Node("b"))
    lmdb_graph_db.put_edge(Edge("e", "a", "b", {"key": "user key", "values": [2]}))
    result = to_networkx(lmdb_graph_db)
    assert result.nodes["a"]["__gestaltdb_networkx__"]["labels"] == ["A"]
    assert result.edges["a", "b", "e"]["key"] == "user key"
    result.nodes["a"]["values"].append(9)
    result.edges["a", "b", "e"]["values"].append(9)
    assert lmdb_graph_db.get_node(b"a").properties["values"] == [1]
    assert lmdb_graph_db.get_edge(b"e").properties["values"] == [2]


def test_gexf_undirected_defaults(lmdb_graph_db):
    data = gexf(body='''<attributes class="edge"><attribute id="s" title="score" type="double"><default>3.5</default></attribute></attributes>
    <nodes><node id="a"/><node id="b"/></nodes><edges><edge id="e" source="a" target="b"/></edges>''')
    data = data.replace(b'defaultedgetype="directed"', b'defaultedgetype="undirected"')
    with pytest.raises(ValueError, match="Undirected"):
        load_gexf(io.BytesIO(data), lmdb_graph_db)
    result = load_gexf(io.BytesIO(data), lmdb_graph_db, undirected="bidirectional")
    assert result.imported_edges == 2
    assert lmdb_graph_db.get_edge(b"e").properties["score"] == 3.5
