#!/usr/bin/env python3
"""Compatibility wrapper forwarding to benchmarks.tuning."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from benchmarks.tuning import main

if __name__ == "__main__":
    main()
