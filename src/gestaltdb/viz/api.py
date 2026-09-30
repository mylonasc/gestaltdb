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
    """Labels, property styling, caps, and initial notebook-view settings.

    ``node_label_property`` / ``edge_label_property`` choose readable labels
    with ID/type fallbacks. ``edge_labels`` is ``off``, ``selected`` (also
    hovered/focused), or ``all``. Size/width properties use bounded numeric
    scales; color properties use categorical values. These mappings also
    apply to static SVG, where relationship labels require ``edge_labels='all'``.
    """

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
    node_label_property: str | None = None
    edge_label_property: str | None = None
    edge_labels: str = "selected"
    label_max_length: int = 40
    node_size_property: str | None = None
    node_color_property: str | None = None
    edge_width_property: str | None = None
    edge_color_property: str | None = None

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
        if self.edge_labels not in ("off", "selected", "all"):
            raise ValueError("edge_labels must be 'off', 'selected', or 'all'")
        if not isinstance(self.label_max_length, int) or self.label_max_length < 1:
            raise ValueError("label_max_length must be a positive integer")
        for name in ("node_label_property", "edge_label_property", "node_size_property",
                     "node_color_property", "edge_width_property", "edge_color_property"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty string or None")

    def view_overrides(self) -> dict[str, Any]:
        """Return the front-end initial-state overrides for this options set."""
        return {
            "theme": self.theme,
            "charge": self.charge,
            "linkDistance": self.link_distance,
            "showLabels": self.show_labels,
            "showProperties": self.show_properties,
            "nodeLabelProperty": self.node_label_property,
            "edgeLabelProperty": self.edge_label_property,
            "edgeLabels": self.edge_labels,
            "labelMaxLength": self.label_max_length,
            "nodeSizeProperty": self.node_size_property,
            "nodeColorProperty": self.node_color_property,
            "edgeWidthProperty": self.edge_width_property,
            "edgeColorProperty": self.edge_color_property,
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

    def as_svg(self, *, width: int = 1200, height: int = 800, layout: str = "circle",
               positions: Mapping[str, tuple[float, float]] | None = None, background: bool = True) -> str:
        """Return dependency-free SVG using a static layout or supplied coordinates.

        This renders the Python graph, not the current notebook iframe state.
        Set ``edge_labels='all'`` to include relationship labels in static output.
        """
        from .svg import render_svg

        return render_svg(self.viz, self.options, width=width, height=height,
                          layout=layout, positions=positions, background=background)

    def save_svg(self, path: str | Path, **kwargs: Any) -> Path:
        """Write :meth:`as_svg` output and return the resolved destination."""
        document = self.as_svg(**kwargs)
        resolved = Path(path).expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(document, encoding="utf-8")
        return resolved

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
