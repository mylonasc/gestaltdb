"""Packaged visualization surface for GestaltDB graphs.

The ``viz`` subpackage converts property graphs, Cypher results, and sampled
ML subgraphs into a deterministic JSON-serializable intermediate
representation (IR) that feeds the prebuilt React + D3 front end and
self-contained offline HTML artifacts. ``VizOptions`` configures readable
node/relationship labels and property-driven node size/color and edge
width/color. The viewer supports directed exploration, local saved views,
and SVG/PNG downloads.

The Python side uses only the standard library so library users never need
a JavaScript toolchain at runtime.
``VizFigure.as_svg()`` / ``save_svg()`` also render static circle/grid layouts
or supplied coordinates, independently of notebook iframe state. Retrieve
runnable installed-library examples with
``python -m gestaltdb.agent_docs get visualization --examples``.
"""

from .api import (
    VizFigure,
    VizOptions,
    visualize_nodes_edges,
    visualize_query,
    visualize_sample,
    visualize_sampler_batch,
)
from .html import build_html, ensure_bundle_fresh, is_bundle_fresh, read_manifest, save_html
from .ir import (
    DEFAULT_ABSOLUTE_CEILING_MULTIPLIER,
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    VIZ_IR_VERSION,
    TruncationInfo,
    TruncationWarning,
    VizCapExceededError,
    VizEdge,
    VizGraph,
    VizNode,
    warn_if_truncated,
)

__all__ = [
    "DEFAULT_ABSOLUTE_CEILING_MULTIPLIER",
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_NODES",
    "VIZ_IR_VERSION",
    "TruncationInfo",
    "TruncationWarning",
    "VizCapExceededError",
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
    "visualize_sampler_batch",
    "warn_if_truncated",
]
