"""Deterministic static SVG figures for the onto-tutorial (stdlib only)."""

from __future__ import annotations

from pathlib import Path


def subsumption_svg(classes: list[str], edges: list[tuple[str, str]]) -> str:
    """Render a minimal top-down lattice as dependency-free SVG."""
    w, h = 480, 120 + 48 * max(1, len(classes))
    y = {c: 60 + 48 * i for i, c in enumerate(classes)}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">']
    for a, b in edges:
        parts.append(
            f'<line x1="240" y1="{y[a]}" x2="240" y2="{y[b]}" '
            'stroke="black" stroke-width="1.5" marker-end="url(#a)"/>'
        )
    for c in classes:
        parts.append(
            f'<rect x="170" y="{y[c]-14}" width="140" height="28" fill="white" '
            f'stroke="black"/><text x="240" y="{y[c]+5}" text-anchor="middle" '
            f'font-size="13">{c}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def save_svg(document: str, path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(document, encoding="utf-8")
    return resolved
