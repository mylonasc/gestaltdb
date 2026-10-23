"""Include the portable skill's tests in this repository's normal pytest run."""

from pathlib import Path
import runpy

_tests = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                           ".opencode/skills/issue-atlas/tests/test_build.py"))
globals().update({name: value for name, value in _tests.items() if name.startswith("test_")})
