"""Consumer-side mypy checks, optional outside the lint environment."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_static_consumer_contracts():
    pytest.importorskip("mypy")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, "-m", "mypy", "--follow-imports=silent",
            "--strict", "--no-incremental", "--cache-dir=/dev/null",
            "tests/typing/storage_sampling.py",
            "tests/typing/temporal.py",
            "tests/typing/cypher_execution.py",
            "tests/typing/cypher_parser.py",
        ],
        cwd=root, capture_output=True, text=True,
    )
    # Strict mode checks assert_type and makes every negative ignore necessary.
    # Followed implementation diagnostics belong to the separate library run.
    assert result.returncode == 0, result.stdout + result.stderr
