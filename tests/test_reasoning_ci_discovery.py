"""New reasoning modules must not silently fall outside the ontology CI gate."""

from pathlib import Path


def test_ontology_ci_discovers_all_reasoning_and_ontology_modules():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ontology.yml").read_text()
    gate = workflow.split("- name: Run standalone, adapter and oracle gates", 1)[1].split("- name:", 1)[0]
    assert "tests/test_reasoning*.py" in gate
    assert "tests/test_ontology*.py" in gate
    assert "tests/test_reasoning_engine.py" not in gate
    # Verify the discovery patterns include the integration regressions that
    # exposed why a hand-maintained list was unsafe.
    selected = {path.name for pattern in ("test_reasoning*.py", "test_ontology*.py")
                for path in (root / "tests").glob(pattern)}
    assert {"test_reasoning_typed.py", "test_reasoning_ranges.py", "test_reasoning_generations.py"} <= selected
