"""Keep the public demand example executable, not merely syntactically valid."""

from pathlib import Path
import textwrap


def test_demand_quickstart_runs():
    path = Path(__file__).resolve().parents[1] / "docs" / "reasoning-demand.rst"
    text = path.read_text(encoding="utf-8")
    _, example = text.split(".. code-block:: python\n", 1)
    example, _ = example.split("\nHow it works", 1)
    exec(compile(textwrap.dedent(example), str(path), "exec"), {})
