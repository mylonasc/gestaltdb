#!/usr/bin/env python3
"""Generate a structured API index for the packaged agent docs search tool.

Parses ``src/gestaltdb/graphdb.py`` with ``ast`` and emits
``src/gestaltdb/agent_skill/api_index.json``: one entry per public ``GraphDB``
method with its signature, docstring summary, ``Returns:`` note, a
heuristically assigned docs topic, source line, and packaged examples that
demonstrate it.

Topic assignment is intentionally heuristic (ordered name-based rules, first
match wins) so the index stays in sync with the code without hand curation.
Run with ``--check`` in CI/docs checks to fail when the committed index is
stale; run with ``--write`` (default) to regenerate it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parents[2]
GRAPHDB_PATH = REPO_ROOT / "src" / "gestaltdb" / "graphdb.py"
SKILL_RESOURCE_DIR = REPO_ROOT / "src" / "gestaltdb" / "agent_skill"
INDEX_PATH = SKILL_RESOURCE_DIR / "api_index.json"

_EXAMPLE_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def topic_for_method(name: str) -> str:
    """Heuristically map a GraphDB method name to a packaged docs topic."""
    if name == "query" or "cypher" in name:
        return "cypher"
    if (
        name.startswith("sample_")
        or "typed_adjacency" in name
        or name.startswith("neighbors_")
        or name in {"edges_by_edge_type", "get_typed_adjacency", "edge_type"}
    ):
        return "sampling"
    if (
        name.startswith(("create_", "rebuild", "nodes_by", "edges_by", "iter_node", "iter_edge", "count_"))
        or "index" in name
    ):
        return "indexing"
    if name.startswith("ingest_") or name in {"serialize_node_value", "serialize_edge_value"}:
        return "ingestion"
    if name in {"create", "open", "manifest", "save_manifest"}:
        return "backends"
    return "quickstart"


def _format_arg(arg: ast.arg, default: ast.expr | None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        try:
            text += f": {ast.unparse(arg.annotation)}"
        except Exception:  # noqa: BLE001 - keep the index builder robust.
            pass
    if default is not None:
        try:
            rendered = ast.unparse(default)
        except Exception:  # noqa: BLE001 - keep the index builder robust.
            rendered = "..."
        if len(rendered) > 40:
            rendered = rendered[:37] + "..."
        text += f"={rendered}"
    return text


def _format_signature(node: ast.FunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        if arg.arg in {"self", "cls"}:
            continue
        parts.append(_format_arg(arg, default))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_format_arg(arg, default))
    if args.vararg is not None:
        parts.append(f"*{args.vararg.arg}")
    if args.kwarg is not None:
        parts.append(f"**{args.kwarg.arg}")
    return f"{node.name}({', '.join(parts)})"


def _split_docstring(doc: str) -> tuple[str, str]:
    summary = ""
    returns = ""
    lines = doc.strip().splitlines()
    if lines:
        summary = lines[0].strip()
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("returns"):
            for follow in lines[index + 1 :]:
                if follow.strip():
                    returns = follow.strip()
                    break
            break
    return summary, returns


def _example_coverage() -> dict[str, list[dict[str, object]]]:
    """Map method names to packaged examples that demonstrate them."""
    docs: list[tuple[str, str]] = [("skill", (SKILL_RESOURCE_DIR / "SKILL.md").read_text(encoding="utf-8"))]
    for path in sorted((SKILL_RESOURCE_DIR / "references").glob("*.md")):
        docs.append((path.stem, path.read_text(encoding="utf-8")))
    coverage: dict[str, list[dict[str, object]]] = {}
    for doc_name, content in docs:
        for example_index, block in enumerate(_EXAMPLE_BLOCK_RE.findall(content), 1):
            for match in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", block)):
                coverage.setdefault(match, []).append({"doc": doc_name, "example": example_index})
    return coverage


def build_index() -> dict:
    """Build the API index from the GraphDB source file."""
    source = GRAPHDB_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    coverage = _example_coverage()
    methods = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "GraphDB":
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_"):
                continue
            doc = ast.get_docstring(item) or ""
            summary, returns = _split_docstring(doc)
            methods.append(
                {
                    "name": item.name,
                    "signature": _format_signature(item),
                    "summary": summary,
                    "returns": returns,
                    "topic": topic_for_method(item.name),
                    "line": item.lineno,
                    "examples": coverage.get(item.name, []),
                }
            )
    methods.sort(key=lambda entry: entry["name"])
    return {
        "generated_from": {
            "path": "src/gestaltdb/graphdb.py",
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        },
        "methods": methods,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the packaged agent-docs API index.")
    parser.add_argument("--write", action="store_true", help="regenerate src/gestaltdb/agent_skill/api_index.json (default)")
    parser.add_argument("--check", action="store_true", help="exit nonzero when the committed index is stale")
    args = parser.parse_args(argv)
    index = build_index()
    if args.check:
        if not INDEX_PATH.exists():
            print(f"missing {INDEX_PATH.relative_to(REPO_ROOT)}; run generate_api_index.py --write", file=sys.stderr)
            return 1
        committed = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if committed != index:
            print(
                "api_index.json is stale; run "
                "uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/generate_api_index.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"api_index.json is fresh ({len(index['methods'])} methods)")
        return 0
    INDEX_PATH.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({len(index['methods'])} methods)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
