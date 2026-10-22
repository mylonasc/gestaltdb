"""Snippet and viz helpers re-exported by the onto-tutorial toolkit."""

from __future__ import annotations

from typing import Any


def visualize_tutorial_graph(nodes: Any, edges: Any, **caps: Any):  # pragma: no cover
    """Build a VizFigure with tutorial caps (2000 nodes / 5000 edges)."""
    from gestaltdb.viz.api import VizOptions, visualize_nodes_edges

    options = VizOptions(edge_labels="all")
    if caps:
        options = VizOptions(edge_labels="all", **caps)
    return visualize_nodes_edges(nodes, edges, options=options)
