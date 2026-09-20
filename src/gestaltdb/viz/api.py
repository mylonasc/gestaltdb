"""Public Python API for packaged graph visualization (VIZ-06).

Thin conveniences over the IR (VIZ-01) and HTML (VIZ-03) layers. Standard
library only; everything heavy stays behind function calls so
``import gestaltdb`` never pays for visualization.
"""

from __future__ import annotations

import html as html_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class VizOptions:
    """Rendering options shared by static files and notebook cells."""

    max_nodes: int = 2000
    max_edges: int = 5000
    absolute_max_nodes: int | None = None
    absolute_max_edges: int | None = None
    height: int = 600
    theme: str = "light"
    charge: float = -300.0
    link_distance: float = 60.0
    title: str = "GestaltDB graph"
    show_labels: bool = True
    show_properties: bool = True

    def __post_init__(self) -> None:
        """Validate option ranges eagerly with actionable messages."""
        if self.max_nodes <= 0 or self.max_edges <= 0:
            raise ValueError("max_nodes and max_edges must be positive")
        if self.absolute_max_nodes is not None and self.absolute_max_nodes <= 0:
            raise ValueError("absolute_max_nodes must be positive")
        if self.absolute_max_edges is not None and self.absolute_max_edges <= 0:
            raise ValueError("absolute_max_edges must be positive")
        if self.height <= 0:
            raise ValueError("height must be positive")
        if self.theme not in ("light", "dark"):
            raise ValueError("theme must be 'light' or 'dark'")

    def view_overrides(self) -> dict[str, Any]:
        """Return the front-end initial-state overrides for this options set."""
        return {
            "theme": self.theme,
            "charge": self.charge,
            "linkDistance": self.link_distance,
            "showLabels": self.show_labels,
            "showProperties": self.show_properties,
        }


@dataclass
class VizFigure:
    """A built visualization: save it, embed it, or display it in Jupyter."""

    viz: Any
    options: VizOptions = field(default_factory=VizOptions)

    def as_html(self) -> str:
        """Return the self-contained offline HTML document."""
        from .html import build_html

        return build_html(
            self.viz,
            title=self.options.title,
            view_overrides=self.options.view_overrides(),
        )

    def save(self, path: str | Path) -> Path:
        """Write the artifact to ``path`` and return the resolved path."""
        from .html import save_html

        return save_html(
            self.viz,
            path,
            title=self.options.title,
            view_overrides=self.options.view_overrides(),
        )

    def _repr_html_(self) -> str:
        """Render inline in Jupyter via a sandboxed-height ``srcdoc`` iframe."""
        document = html_module.escape(self.as_html(), quote=True)
        height = int(self.options.height)
        return (
            f'<iframe srcdoc="{document}" width="100%" height="{height}" '
            'frameborder="0" style="border:1px solid #ccc;border-radius:4px;" '
            'title="GestaltDB graph visualization"></iframe>'
        )

    def __repr__(self) -> str:
        """Return a one-line summary without rendering the document."""
        nodes = len(getattr(self.viz, "nodes", ()) or ())
        edges = len(getattr(self.viz, "edges", ()) or ())
        truncated = getattr(getattr(self.viz, "truncation", None), "truncated", False)
        suffix = " (truncated by caps)" if truncated else ""
        return f"VizFigure(nodes={nodes}, edges={edges}{suffix})"


def _coerce_options(options: VizOptions | Mapping[str, Any] | None) -> VizOptions:
    if options is None:
        return VizOptions()
    if isinstance(options, VizOptions):
        return options
    if isinstance(options, Mapping):
        return VizOptions(**dict(options))
    raise TypeError(f"options must be VizOptions or a mapping, got {type(options).__name__}")


def _caps_kwargs(resolved: VizOptions) -> dict[str, Any]:
    return {
        "max_nodes": resolved.max_nodes,
        "max_edges": resolved.max_edges,
        "absolute_max_nodes": resolved.absolute_max_nodes,
        "absolute_max_edges": resolved.absolute_max_edges,
    }


def _finish(viz: Any, resolved: VizOptions, *, source: str) -> VizFigure:
    from .ir import warn_if_truncated

    warn_if_truncated(viz, source=source)
    return VizFigure(viz=viz, options=resolved)


def visualize_nodes_edges(
    nodes: Any,
    edges: Any,
    *,
    options: VizOptions | Mapping[str, Any] | None = None,
    **caps: Any,
) -> VizFigure:
    """Visualize explicit node/edge collections.

    Args:
        nodes: ``Node`` objects or node-shaped dicts.
        edges: ``Edge`` objects or edge-shaped dicts.
        options: ``VizOptions`` or equivalent mapping.
        caps: Optional ``max_nodes``/``max_edges`` overrides.
    """
    from .ir import VizGraph

    if caps:
        merged = dict(_coerce_options(options).__dict__)
        merged.update(caps)
        resolved = VizOptions(**merged)
    else:
        resolved = _coerce_options(options)
    return _finish(
        VizGraph.from_nodes_edges(nodes, edges, **_caps_kwargs(resolved)),
        resolved,
        source="visualize_nodes_edges",
    )


def visualize_query(
    graph: Any,
    cypher: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    options: VizOptions | Mapping[str, Any] | None = None,
) -> VizFigure:
    """Run a Cypher query and visualize the matched entities."""
    from .ir import VizGraph

    resolved = _coerce_options(options)
    result = graph.query(cypher, parameters=dict(parameters) if parameters else None)
    return _finish(
        VizGraph.from_cypher_result(result, **_caps_kwargs(resolved)),
        resolved,
        source="visualize_query",
    )


def visualize_sample(
    graph: Any,
    seeds: Any,
    pattern: Any,
    *,
    rng: Any = None,
    options: VizOptions | Mapping[str, Any] | None = None,
) -> VizFigure:
    """Sample a typed subgraph around ``seeds`` and visualize it."""
    from .ir import VizGraph

    resolved = _coerce_options(options)
    subgraph = graph.sample_typed_subgraph(seeds, pattern, rng=rng)
    return _finish(
        VizGraph.from_sampled_subgraph(subgraph, **_caps_kwargs(resolved)),
        resolved,
        source="visualize_sample",
    )


def visualize_sampler_batch(
    batch: Any,
    snapshot: Any,
    *,
    options: VizOptions | Mapping[str, Any] | None = None,
) -> VizFigure:
    """Visualize an ML training batch mapped back to external IDs.

    Local batch rows resolve through ``batch.node_ids_global`` to compact
    global IDs and then via ``snapshot.external_node_id(...)`` /
    ``snapshot.external_relation_id(...)``. The sampling-first path for
    inspecting ``SamplerEngine`` output without dumping whole snapshots.
    """
    from .ir import VizGraph

    resolved = _coerce_options(options)
    return _finish(
        VizGraph.from_sampler_batch(batch, snapshot, **_caps_kwargs(resolved)),
        resolved,
        source="visualize_sampler_batch",
    )
