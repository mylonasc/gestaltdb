"""Packaged visualization surface for GestaltDB graphs.

The ``viz`` subpackage converts property graphs, Cypher results, and sampled
ML subgraphs into a deterministic JSON-serializable intermediate
representation (IR) that feeds the prebuilt D3.js + React front end
(``VIZ-02`` onward) and self-contained offline HTML artifacts (``VIZ-03``).

The Python side uses only the standard library so library users never need
a JavaScript toolchain at runtime.
"""

from .api import VizFigure, VizOptions, visualize_nodes_edges, visualize_query, visualize_sample
from .html import build_html, ensure_bundle_fresh, is_bundle_fresh, read_manifest, save_html
from .ir import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    VIZ_IR_VERSION,
    TruncationInfo,
    VizEdge,
    VizGraph,
    VizNode,
)

__all__ = [
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_NODES",
    "VIZ_IR_VERSION",
    "TruncationInfo",
    "VizEdge",
    "VizGraph",
    "VizNode",
    "VizFigure",
    "VizOptions",
    "build_html",
    "ensure_bundle_fresh",
    "is_bundle_fresh",
    "read_manifest",
    "save_html",
    "visualize_nodes_edges",
    "visualize_query",
    "visualize_sample",
]
