"""Static output must be standalone XML, deterministic, and faithfully attributed."""
from xml.etree import ElementTree as ET

import pytest

from gestaltdb.viz.api import VizFigure, VizOptions, visualize_nodes_edges

NS = {"s": "http://www.w3.org/2000/svg"}


def figure(**options):
    return visualize_nodes_edges(
        [{"id": "a", "labels": ["Person"], "properties": {"name": "Alice <&>"}}, {"id": "b"}],
        [{"id": "r1", "source": "a", "target": "b", "properties": {"type": "KNOWS", "score": 0.5}},
         {"id": "r2", "source": "b", "target": "a", "properties": {"type": "LIKES"}},
         {"id": "r3", "source": "a", "target": "a", "properties": {"type": "SELF"}}],
        options=VizOptions(node_label_property="name", edge_labels="all", **options))


def test_standalone_svg_preserves_labels_directions_and_parallel_edges(tmp_path):
    fig = figure(theme="dark", edge_label_property="score")
    svg = fig.as_svg()
    assert fig.save_svg(tmp_path / "graph.svg").read_text() == svg
    assert svg == fig.as_svg()
    root = ET.fromstring(svg)
    assert root.attrib["viewBox"] == "0 0 1200 800"
    labels = [element.text for element in root.findall(".//s:text", NS)]
    assert "Alice <&>" in labels and "0.5" in labels and "LIKES" in labels
    paths = root.findall("s:g/s:path", NS)
    assert len(paths) == 3
    assert len({element.attrib["d"] for element in paths}) == 3
    assert all("marker-end" in element.attrib for element in paths)
    assert root.find("s:rect", NS).attrib["fill"] == "#14161a"


def test_coordinates_empty_graph_and_transparent_output():
    fig = figure()
    svg = fig.as_svg(positions={"a": (0, 0), "b": (1, 0)}, background=False)
    assert ET.fromstring(svg).find("s:rect", NS) is None
    circles = ET.fromstring(svg).findall("s:g/s:circle", NS)
    assert circles[0].attrib["cy"] == circles[1].attrib["cy"]
    assert ET.fromstring(visualize_nodes_edges([], []).as_svg()).tag.endswith("svg")
    assert fig.as_svg(layout="grid") != fig.as_svg(layout="circle")


@pytest.mark.parametrize("kwargs", [{"width": 0}, {"height": 1.5}, {"layout": "unknown"},
                                  {"positions": {}}, {"positions": {"a": (float("nan"), 0), "b": (1, 0)}}])
def test_svg_rejects_invalid_rendering_inputs(kwargs):
    with pytest.raises(ValueError):
        figure().as_svg(**kwargs)


def test_static_labels_respect_visibility_and_truncation():
    fig = figure(show_labels=False, label_max_length=3)
    labels = [element.text for element in ET.fromstring(fig.as_svg()).findall(".//s:text", NS)]
    assert "Alice <&>" not in labels
    assert "KN…" in labels


@pytest.mark.parametrize("kwargs", [{"edge_labels": "invalid"}, {"label_max_length": 0},
                                  {"node_label_property": ""}, {"edge_label_property": 3}])
def test_label_options_validation(kwargs):
    with pytest.raises(ValueError):
        VizOptions(**kwargs)


def test_static_attribute_styles_have_bounded_sizes_and_consistent_colors():
    fig = visualize_nodes_edges(
        [{"id": "a", "properties": {"mass": 1, "kind": "shared"}},
         {"id": "b", "properties": {"mass": 9, "kind": "shared"}},
         {"id": "c", "properties": {"mass": True}}],
        [{"id": "ab", "source": "a", "target": "b", "properties": {"score": 1, "color": "shared"}},
         {"id": "bc", "source": "b", "target": "c", "properties": {"score": 3, "color": "shared"}}],
        options=VizOptions(node_size_property="mass", node_color_property="kind",
                           edge_width_property="score", edge_color_property="color"))
    root = ET.fromstring(fig.as_svg())
    circles = root.findall("s:g/s:circle", NS)
    assert [float(circle.attrib["r"]) for circle in circles] == [8, 32, 12]
    assert circles[0].attrib["fill"] == circles[1].attrib["fill"]
    paths = root.findall("s:g/s:path", NS)
    assert [float(path.attrib["stroke-width"]) for path in paths] == [1, 7]
    assert paths[0].attrib["stroke"] == paths[1].attrib["stroke"]
    assert paths[0].attrib["marker-end"] == paths[1].attrib["marker-end"]


def test_numeric_styles_remain_finite_for_extreme_property_values():
    fig = visualize_nodes_edges(
        [{"id": "a", "properties": {"mass": -1e308}}, {"id": "b", "properties": {"mass": 1e308}},
         {"id": "c", "properties": {"mass": 10 ** 1000}}], [], options=VizOptions(node_size_property="mass"))
    circles = ET.fromstring(fig.as_svg()).findall("s:g/s:circle", NS)
    assert [float(circle.attrib["r"]) for circle in circles] == [8, 32, 12]
    svg = fig.as_svg(positions={"a": (-1e308, 0), "b": (1e308, 0), "c": (0, 0)})
    assert "nan" not in svg.lower() and "inf" not in svg.lower()


def test_tree_layout_layers_predecessors_above_dependents():
    fig = visualize_nodes_edges(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        [{"id": "ab", "source": "a", "target": "b"},
         {"id": "bc", "source": "b", "target": "c"}])
    circles = ET.fromstring(fig.as_svg(layout="tree")).findall("s:g/s:circle", NS)
    cy = {}
    for circle, node_id in zip(circles, ("a", "b", "c")):
        title = circle.find("s:title", NS)
        assert title is not None and title.text == node_id
        cy[node_id] = float(circle.attrib["cy"])
    assert cy["a"] < cy["b"] < cy["c"]
    assert fig.as_svg(layout="tree") == fig.as_svg(layout="tree")
    assert fig.as_svg(layout="tree") != fig.as_svg(layout="circle")


def test_tree_layout_degrades_gracefully_on_cycles_and_self_loops():
    fig = visualize_nodes_edges(
        [{"id": "a"}, {"id": "b"}],
        [{"id": "ab", "source": "a", "target": "b"},
         {"id": "ba", "source": "b", "target": "a"},
         {"id": "aa", "source": "a", "target": "a"}])
    circles = ET.fromstring(fig.as_svg(layout="tree")).findall("s:g/s:circle", NS)
    assert len(circles) == 2
    assert float(circles[0].attrib["cy"]) != float(circles[1].attrib["cy"])


def test_static_labels_use_readable_font_size():
    sizes = {element.attrib["font-size"]
             for element in ET.fromstring(figure().as_svg()).findall(".//s:text", NS)}
    assert sizes == {"13"}


def test_static_shapes_and_dashes_match_vocabulary():
    from gestaltdb.viz.ir import VizGraph

    viz = VizGraph.from_nodes_edges(
        [{"id": "fact", "labels": ["derived"], "shape": "triangle"},
         {"id": "rule", "labels": ["rule"], "shape": "square"},
         {"id": "node", "labels": ["Person"]}],
        [{"id": "d1", "source": "fact", "target": "rule", "properties": {"type": "derived_by"}, "dash": "6 4"},
         {"id": "s1", "source": "rule", "target": "node", "properties": {"type": "premise"}}])
    root = ET.fromstring(VizFigure_for(viz).as_svg())
    assert len(root.findall("s:g/s:polygon", NS)) == 1
    assert len(root.findall("s:g/s:rect", NS)) == 1
    assert len(root.findall("s:g/s:circle", NS)) == 1
    dashed = [path.attrib.get("stroke-dasharray") for path in root.findall("s:g/s:path", NS)]
    assert dashed == ["6 4", None]
    assert ET.fromstring(VizFigure_for(viz).as_svg()).findall("s:g/s:polygon", NS)[0].find("s:title", NS) is not None


def VizFigure_for(viz):
    return VizFigure(viz=viz)

