"""Emit the production offline artifact used by browser regression tests."""
from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

nodes = [{"id": "a", "labels": ["Person"], "properties": {"name": "Alice", "mass": 1}},
         {"id": "b", "labels": ["Person", "Partner"], "properties": {"name": "Bob", "mass": 9}},
         {"id": "c", "labels": ["Hidden"], "properties": {"name": "Carol"}}]
edges = [{"id": "r1", "source": "a", "target": "b", "properties": {"type": "KNOWS", "score": 1}},
         {"id": "r2", "source": "b", "target": "a", "properties": {"type": "LIKES", "score": 10}},
         {"id": "link", "source": "b", "target": "c", "properties": {"type": "LINK"}},
         {"id": "parallel", "source": "a", "target": "b", "properties": {"type": "KNOWS"}},
         {"id": "loop2", "source": "a", "target": "a", "properties": {"type": "SELF"}},
         {"id": "loop", "source": "a", "target": "a", "properties": {"type": "SELF", "score": 0.75}}]
print(visualize_nodes_edges(nodes, edges, options=VizOptions(node_label_property="name", edge_labels="all")).as_html())
