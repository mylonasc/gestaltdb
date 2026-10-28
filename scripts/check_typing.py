#!/usr/bin/env python3
"""Run the static typing ratchet for the incrementally typed library modules.

The lint workflow installs mypy, so this gate must actually execute instead of
relying on an ``importorskip`` inside the test suite. The module set is read
from ``pyproject.toml``: every ``[[tool.mypy.overrides]]`` block that turns on
``disallow_untyped_defs`` must pass under ``--follow-imports=silent``.
Diagnostics in not-yet-typed modules stay out of scope, matching the issue-410
incremental rollout.

Usage::

    python scripts/check_typing.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SRC = ROOT / "src"


def strict_modules(pyproject_text: str) -> list[str]:
    """Return modules from overrides that require fully typed definitions."""
    modules: list[str] = []
    for block in pyproject_text.split("[[tool.mypy.overrides]]")[1:]:
        if "disallow_untyped_defs = true" not in block:
            continue
        match = re.search(r"^module = (\[.*?\])", block, re.S | re.M)
        if match is None:
            raise SystemExit("mypy override with disallow_untyped_defs has no module list")
        modules.extend(re.findall(r'"([\w.]+)"', match.group(1)))
    return sorted(set(modules))


def module_path(module: str) -> Path:
    base = SRC.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"strictly typed module {module!r} has no source file under src/")


def main() -> int:
    modules = strict_modules(PYPROJECT.read_text(encoding="utf-8"))
    if not modules:
        raise SystemExit("no strictly typed mypy overrides found in pyproject.toml")
    paths = [str(module_path(module)) for module in modules]
    command = [
        sys.executable, "-m", "mypy", "--follow-imports=silent",
        "--no-incremental", "--cache-dir=/dev/null", *paths,
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
