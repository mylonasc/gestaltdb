#!/usr/bin/env python3
"""Compatibility wrapper forwarding to benchmarks.quick."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmarks.quick import main

if __name__ == "__main__":
    main()
