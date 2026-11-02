"""Prevent development-diary language from leaking into library-facing guides.

This catches known leaks, not semantic staleness or all editorial defects.
Contributor-only quality guidance and archived planning reports stay separate.
"""

import ast
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    r"\b(?:RE|ONTO|HSA|RWS|DOC|VIS)-(?:\d+|E\d+)\b",
    r"\bthis (?:slice|milestone|pass)\b",
    r"\broadmap\b",
    r"\bv1 (?:backend )?decision\b",
    r"\bPyRex Point-Read Regression\b",
    r"\b(?:EPIC_[A-Z_]+\.md|onto-dev)\b",
    r"gestaltdb(?:\[[^]]+\])?==0\.13\.0rc1",
]
FORBIDDEN = re.compile("|".join(PATTERNS), re.IGNORECASE)
PAGES = sorted((ROOT / "docs").glob("*.rst")) + [ROOT / "README.md", ROOT / "EXAMPLES.md"]
PAGES += sorted((ROOT / "src/gestaltdb/agent_skill").rglob("*.md"))


@pytest.mark.parametrize("path", PAGES, ids=lambda path: str(path.relative_to(ROOT)))
def test_library_docs_do_not_narrate_implementation_progress(path):
    violations = [(number, line.strip()) for number, line in
                  enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                  if FORBIDDEN.search(line)]
    assert not violations, f"Describe current library behavior instead of development progress: {violations}"


def test_editorial_guard_preserves_compatibility_and_benchmark_identity():
    for text in (
        "indexed-python-v1", "gestaltdb-relational-v1", "pyrex-rocksdb>=0.7.1",
        "benchmarks/results/reasoning-re03-comparison.json", "valid_time=50",
    ):
        assert not FORBIDDEN.search(text)
    for text in ("V1 backend decision", "RE-06 selects", "This slice delivers", "onto-dev"):
        assert FORBIDDEN.search(text)


def test_api_docstrings_do_not_narrate_implementation_progress():
    violations = []
    for path in sorted((ROOT / "src/gestaltdb").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node)
            if doc and (match := FORBIDDEN.search(doc)):
                violations.append((str(path.relative_to(ROOT)), getattr(node, "lineno", 1), match[0]))
    assert not violations, violations
