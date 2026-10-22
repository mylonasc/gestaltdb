"""PropEnh-14 (#127) M1: relation-subproperty hierarchies for sampler expansion."""

import pytest

from gestaltdb.sampling import RelationHierarchy, SamplerEngine, SamplerSnapshot


def _hierarchy():
    return RelationHierarchy({
        "Ocean_Freight": ["Transport_Edge"],
        "Air_Freight": ["Transport_Edge"],
        "Rail": ["Transport_Edge"],
        "Refrigerated_Freight": ["Ocean_Freight"],
    })


def _snapshot(tmp_path):
    return SamplerSnapshot.from_arrays(
        tmp_path / "hier",
        external_node_ids=["a", "b", "c", "d"],
        node_type_ids=[-1] * 4,
        external_edge_ids=["e1", "e2", "e3"],
        external_relation_ids=["Transport_Edge", "Ocean_Freight", "Rail"],
        src_int=[0, 1, 2],
        dst_int=[1, 2, 3],
        rel_int=[0, 1, 2],
    )


def test_closure_expands_parents_to_transitive_children():
    hierarchy = _hierarchy()
    assert hierarchy.closure("Transport_Edge") == (
        "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight",
        "Air_Freight", "Rail")
    assert hierarchy.closure("Ocean_Freight") == ("Ocean_Freight", "Refrigerated_Freight")
    assert hierarchy.closure("Rail") == ("Rail",)
    assert hierarchy.closure("Unrelated") == ("Unrelated",)
    assert hierarchy.expand(["Rail", "Ocean_Freight"]) == (
        "Rail", "Ocean_Freight", "Refrigerated_Freight")
    assert hierarchy.parents_of("Ocean_Freight") == ("Transport_Edge",)
    assert hierarchy.parents_of("Transport_Edge") == ()
    assert len(hierarchy) == 5
    assert "Rail" in hierarchy and "Missing" not in hierarchy
    assert hierarchy == _hierarchy()
    assert hash(hierarchy) == hash(_hierarchy())


def test_hierarchy_validates_schema():
    with pytest.raises(ValueError, match="non-empty strings"):
        RelationHierarchy({"": ["P"]})
    with pytest.raises(ValueError, match="non-empty strings"):
        RelationHierarchy({"C": [""]})
    with pytest.raises(ValueError, match="cannot parent itself"):
        RelationHierarchy({"C": ["C"]})
    with pytest.raises(ValueError, match="cycle"):
        RelationHierarchy({"A": ["B"], "B": ["A"]})
    with pytest.raises(ValueError, match="cycle"):
        RelationHierarchy({"A": ["B"], "B": ["C"], "C": ["A"]})
    with pytest.raises(ValueError, match="scalar string"):
        RelationHierarchy({"C": "P"})
    with pytest.raises(ValueError, match="sequence, not a scalar"):
        _hierarchy().expand("Transport_Edge")
    with pytest.raises(ValueError, match="non-empty strings"):
        _hierarchy().closure("")
    assert RelationHierarchy({"C": ["P", "P"]}).parents_of("C") == ("P",)


def test_from_definitions_rejects_duplicates_and_bad_pairs():
    hierarchy = RelationHierarchy.from_definitions([("C", ["P"]), ("D", ["P"])])
    assert hierarchy.closure("P") == ("P", "C", "D")
    with pytest.raises(ValueError, match="duplicate child"):
        RelationHierarchy.from_definitions([("C", ["P"]), ("C", ["Q"])])
    with pytest.raises(ValueError, match="must be a \\(child, parents\\) pair"):
        RelationHierarchy.from_definitions([("C",)])


def test_snapshot_expansion_composes_with_engine_methods(tmp_path):
    snapshot = _snapshot(tmp_path)
    hierarchy = RelationHierarchy({
        "Ocean_Freight": ["Transport_Edge"],
        "Rail": ["Transport_Edge"],
    })
    expanded = snapshot.expand_relations(["Transport_Edge"], hierarchy)
    assert set(snapshot.external_relation_ids[expanded].tolist()) == {
        "Transport_Edge", "Ocean_Freight", "Rail"}

    engine = SamplerEngine(snapshot, seed=5)
    neighbors = engine.sample_neighbors(
        [snapshot.node_int("a")], fanout=10,
        relations=snapshot.expand_relations(["Transport_Edge"], hierarchy))
    assert neighbors.neighbor_nodes.tolist() == [1]

    union = SamplerSnapshot.from_arrays(
        tmp_path / "union", external_node_ids=["c", "x", "y"],
        node_type_ids=[-1] * 3, external_edge_ids=["e1", "e2"],
        external_relation_ids=["Transport_Edge", "Ocean_Freight", "Rail"],
        src_int=[0, 0], dst_int=[1, 2], rel_int=[1, 2])
    union_engine = SamplerEngine(union, seed=5)
    capped = union_engine.sample_neighbors(
        [0], fanout=1, relations=union.expand_relations(["Transport_Edge"], hierarchy))
    assert len(capped.neighbor_nodes) == 1

    with pytest.raises(ValueError, match="unknown snapshot relations"):
        snapshot.expand_relations(["Transport_Edge", "Missing"], hierarchy)
    with pytest.raises(ValueError, match="must be a RelationHierarchy"):
        snapshot.expand_relations(["Transport_Edge"], {"Transport_Edge": []})
