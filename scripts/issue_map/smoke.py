#!/usr/bin/env python3
"""Compatibility entry point for the portable issue-atlas browser smoke."""

from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[2] /
                      ".opencode/skills/issue-atlas/scripts/smoke.py"), run_name="__main__")
