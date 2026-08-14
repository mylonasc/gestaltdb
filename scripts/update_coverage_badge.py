#!/usr/bin/env python3
"""Run tests with coverage and update assets/coverage_badge.svg."""

from __future__ import annotations

import html
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COVERAGE_FILE = ROOT / ".coverage"
BADGE_FILE = ROOT / "assets" / "coverage_badge.svg"


def run_coverage() -> int:
    try:
        import coverage
    except ImportError as exc:
        raise SystemExit(
            "Missing optional dependency 'coverage'. Install it with "
            "`python -m pip install coverage` or `uv add --dev coverage`."
        ) from exc

    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source=src/gestaltdb", "-m", "pytest", "tests"],
        cwd=ROOT,
        check=True,
    )

    cov = coverage.Coverage(data_file=str(COVERAGE_FILE))
    cov.load()
    total = cov.report(show_missing=False, file=None)
    return round(total)


def badge_color(percent: int) -> str:
    if percent >= 90:
        return "#4c1"
    if percent >= 75:
        return "#97CA00"
    if percent >= 60:
        return "#dfb317"
    if percent >= 40:
        return "#fe7d37"
    return "#e05d44"


def text_width(text: str) -> int:
    return 6 * len(text) + 10


def render_badge(label: str, value: str, color: str) -> str:
    left_width = text_width(label)
    right_width = text_width(value)
    total_width = left_width + right_width
    left_center = left_width / 2
    right_center = left_width + right_width / 2
    escaped_label = html.escape(label)
    escaped_value = html.escape(value)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" role="img" aria-label="{escaped_label}: {escaped_value}">
  <title>{escaped_label}: {escaped_value}</title>
  <linearGradient id="smooth" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="round">
    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#round)">
    <rect width="{left_width}" height="20" fill="#555"/>
    <rect x="{left_width}" width="{right_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#smooth)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{left_center}" y="15" fill="#010101" fill-opacity=".3">{escaped_label}</text>
    <text x="{left_center}" y="14">{escaped_label}</text>
    <text x="{right_center}" y="15" fill="#010101" fill-opacity=".3">{escaped_value}</text>
    <text x="{right_center}" y="14">{escaped_value}</text>
  </g>
</svg>
'''


def main() -> int:
    percent = run_coverage()
    BADGE_FILE.write_text(render_badge("test coverage", f"{percent}%", badge_color(percent)))
    print(f"Updated {BADGE_FILE.relative_to(ROOT)} to {percent}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
