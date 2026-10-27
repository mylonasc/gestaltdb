"""Runtime behavior retained by the Cypher execution typing contracts."""

from dataclasses import dataclass

from gestaltdb.graphdb import Node
from gestaltdb.query_engine.cypher.ast import AndExpression, ComparisonExpression, Parameter, PropertyRef
from gestaltdb.query_engine.cypher.runtime import QueryContext, _edge_seek_spec, _is_node
from gestaltdb.query_engine.cypher.write import _with_labels, _with_properties


@dataclass
class NodeDouble:
    get_id: str
    labels: tuple[str, ...]
    properties: dict[str, object]


class ReadDouble:
    indexed_edge_properties = {"score"}

    def __init__(self):
        self.node_reads = 0
        self.edge_reads = 0

    def get_node(self, key):
        self.node_reads += 1
        return None

    def get_edge(self, key):
        self.edge_reads += 1
        return None

    def iter_edge_ids_by_type_property(self, *args):
        raise AssertionError("seek selection must not read the index")

    def iter_edge_ids_by_type_property_range(self, *args):
        raise AssertionError("seek selection must not read the index")


def test_node_guard_keeps_structural_bindings_and_copy_on_write():
    source = NodeDouble("n", ("Old",), {"value": 1})
    assert _is_node(source)
    assert not _is_node({"get_id": "n", "labels": (), "properties": {}})
    updated = _with_properties(source, {"value": 2})
    relabeled = _with_labels(source, ("New",), add=True)
    assert isinstance(updated, Node)
    assert updated.get_id == "n" and updated.properties == {"value": 2}
    assert relabeled.labels == ("Old", "New")
    assert source.properties == {"value": 1}
    assert source.labels == ("Old",)
    mapping = {"value": 1}
    assert _with_properties(mapping, {"other": 2}) == {"value": 1, "other": 2}
    assert mapping == {"value": 1}


def test_query_context_caches_missing_entities():
    graph = ReadDouble()
    context = QueryContext(graph)
    assert context.get_node(b"missing") is None
    assert context.get_node(b"missing") is None
    assert context.get_edge(b"missing") is None
    assert context.get_edge(b"missing") is None
    assert graph.node_reads == graph.edge_reads == 1


def test_typed_edge_seek_specs_preserve_parameters_and_range_bounds():
    context = QueryContext(ReadDouble(), parameters={"value": 3})
    assert _edge_seek_spec("r", (("score", Parameter("value")),), None, context) == ("exact", "score", 3)
    where = AndExpression((
        ComparisonExpression(PropertyRef("r", "score"), ">", 1),
        ComparisonExpression(PropertyRef("r", "score"), "<=", Parameter("value")),
    ))
    assert _edge_seek_spec("r", (), where, context) == ("range", "score", 1, 3, False, True)
    assert _edge_seek_spec("r", (("unindexed", 1),), None, context) is None
