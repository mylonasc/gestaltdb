#!/usr/bin/env python3
"""Run mypy precision analysis and update assets/typing_badge.svg.

The badge reports precise-line coverage over the core library
(``src/gestaltdb``) as measured by ``mypy --lineprecision-report``:
``precise / (lines - empty - unanalyzed)`` summed over all modules.
Untyped function bodies count against precision, so the badge ratchets
with the typing improvements tracked in issue #410.

Recommended invocation (pins the checker and the numeric dependencies
mypy resolves while analyzing the core library)::

    uv run --extra lint --with 'numpy>=1.23' --with 'lark>=1.2' \
        python scripts/update_typing_badge.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BADGE_FILE = ROOT / "assets" / "typing_badge.svg"


def run_precision() -> float:
    try:
        import mypy  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Missing 'mypy'. Install it with `uv run --extra lint` or "
            "`python -m pip install 'mypy==2.4.0'`."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="gestaltdb-typing-") as report_dir:
        # mypy exits non-zero while diagnostics remain; the precision
        # report is still written.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--lineprecision-report",
                report_dir,
                "src/gestaltdb",
            ],
            cwd=ROOT,
        )
        return parse_precision(Path(report_dir) / "lineprecision.txt")


def parse_precision(report: Path) -> float:
    """Aggregate per-module precise lines into one core-library percentage."""
    precise = 0
    denominator = 0
    for line in report.read_text().splitlines()[2:]:
        parts = line.split()
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        _name, lines, precise_lines = parts[0], int(parts[1]), int(parts[2])
        empty = int(parts[5])
        unanalyzed = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
        precise += precise_lines
        denominator += lines - empty - unanalyzed
    if denominator <= 0:
        raise SystemExit(f"Cannot compute typing precision from {report}")
    return 100.0 * precise / denominator


def main() -> int:
    from update_coverage_badge import badge_color, render_badge

    percent = run_precision()
    BADGE_FILE.write_text(render_badge("typing", f"{percent:.1f}%", badge_color(round(percent))))
    print(f"Updated {BADGE_FILE.relative_to(ROOT)} to {percent:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
