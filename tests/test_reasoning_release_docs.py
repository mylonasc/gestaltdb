"""The testing-release entry point must run without optional dependencies."""

from pathlib import Path
import textwrap


def test_testing_release_standalone_example():
    path = Path(__file__).resolve().parents[1] / "docs" / "reasoning-testing.rst"
    text = path.read_text(encoding="utf-8")
    _, example = text.split(".. code-block:: python\n", 1)
    example, _ = example.split("\nThe result is", 1)
    exec(compile(textwrap.dedent(example), str(path), "exec"), {})
