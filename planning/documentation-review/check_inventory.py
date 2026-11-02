"""Validate the documentation capability/page inventory without optional extras.

This checks references and inventory coverage, not runtime semantic correctness.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path("planning/documentation-review")
ROLES = {"overview", "tutorial", "how-to", "explanation", "reference", "operations", "engineering-evidence"}
AUDIENCES = {
    "property-graph developer", "graph-ML developer", "knowledge-graph developer",
    "ontology/Datalog user", "reasoning integrator", "operator", "contributor", "coding agent",
}
DISPOSITIONS = {"keep", "rewrite", "split", "reclassify"}
STATUSES = {"supported", "supported-opt-in", "experimental", "compatibility-only", "policy-or-deferred"}
ENTRY_POINTS = {"README.md", "AGENTS.md", "EXAMPLES.md", "src/gestaltdb/__init__.py"}


def load(root: Path, name: str) -> dict:
    value = json.loads((root / INVENTORY / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"{name}: expected schema_version 1 object")
    return value


def topic_resources(root: Path) -> list[str]:
    """Read the literal registry without importing GestaltDB or optional extras."""
    tree = ast.parse((root / "src/gestaltdb/agent_docs.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOPICS":
            if not isinstance(node.value, ast.Dict):
                break
            result = []
            for value in node.value.values:
                if not isinstance(value, ast.Call) or len(value.args) != 4:
                    raise ValueError("TOPICS: expected literal four-argument Topic entries")
                resource = ast.literal_eval(value.args[3])
                if not isinstance(resource, str):
                    raise ValueError("TOPICS: resource must be a string")
                result.append("src/gestaltdb/agent_skill/" + resource)
            return result
    raise ValueError("TOPICS: literal registry not found")


def strings(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def validate(root: Path) -> list[str]:
    capabilities = load(root, "capabilities.json")
    documents = load(root, "documents.json")
    errors: list[str] = []

    def owner(value: object, context: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"DOC-(?:0[1-9]|1[0-3])", value):
            errors.append(f"{context}: invalid owner {value!r}")

    def paths(value: object, context: str, *, files_only: bool) -> list[str]:
        if not strings(value):
            errors.append(f"{context}: expected nonempty string list")
            return []
        for item in value:
            path = Path(item)
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"{context}: unsafe path {item}")
            elif not (root / path).exists() or (files_only and not (root / path).is_file()):
                errors.append(f"{context}: missing path {item}")
        if len(set(value)) != len(value):
            errors.append(f"{context}: duplicate paths")
        return value

    if not re.fullmatch(r"[0-9a-f]{40}", str(capabilities.get("baseline", ""))):
        errors.append("capabilities: expected immutable baseline commit")
    rows = capabilities.get("capabilities")
    if not isinstance(rows, list) or not rows:
        raise ValueError("capabilities: expected nonempty rows")
    ids = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("capabilities: each row must be an object")
        context = str(row.get("id", "<missing-id>"))
        ids.append(context)
        if not re.fullmatch(r"[a-z][a-z0-9-]*", context):
            errors.append(f"{context}: invalid capability ID")
        owner(row.get("owner"), context)
        if row.get("status") not in STATUSES:
            errors.append(f"{context}: invalid status")
        for field in ("title", "gap"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{context}: missing {field}")
        paths(row.get("sources"), f"{context} sources", files_only=False)
        paths(row.get("tests"), f"{context} tests", files_only=True)
        paths(row.get("existing_docs"), f"{context} existing_docs", files_only=True)
    for name, count in Counter(ids).items():
        if count > 1:
            errors.append(f"duplicate capability: {name}")

    groups = documents.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("documents: expected nonempty groups")
    classified: list[str] = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError("documents: each group must be an object")
        context = f"documents group {index}"
        owner(group.get("owner"), context)
        if group.get("role") not in ROLES or group.get("disposition") not in DISPOSITIONS:
            errors.append(f"{context}: invalid role or disposition")
        if not strings(group.get("audiences")) or not set(group["audiences"]) <= AUDIENCES:
            errors.append(f"{context}: invalid audiences")
        if not isinstance(group.get("reason"), str) or not group["reason"].strip():
            errors.append(f"{context}: missing disposition rationale")
        classified.extend(paths(group.get("files"), context, files_only=True))
    for name, count in Counter(classified).items():
        if count > 1:
            errors.append(f"duplicate document classification: {name}")

    expected = ENTRY_POINTS | {
        path.relative_to(root).as_posix()
        for pattern in ("docs/*.rst", "src/gestaltdb/agent_skill/**/*.md")
        for path in root.glob(pattern)
    }
    for missing in sorted(expected - set(classified)):
        errors.append(f"unclassified document: {missing}")
    for extra in sorted(set(classified) - expected):
        errors.append(f"unexpected document classification: {extra}")
    for row in rows:
        for path in row.get("existing_docs", []):
            # Roadmap evidence is allowed as a coverage pointer, but user guides
            # and packaged resources must also appear in the page inventory.
            if path.startswith(("docs/", "src/gestaltdb/agent_skill/")) and path not in classified:
                errors.append(f"{row['id']}: unclassified coverage pointer {path}")

    resources = topic_resources(root)
    actual = {
        path.relative_to(root).as_posix()
        for path in (root / "src/gestaltdb/agent_skill/references").glob("*.md")
    }
    for resource in sorted(set(resources) - actual):
        errors.append(f"missing topic resource: {resource}")
    for resource in sorted(actual - set(resources)):
        errors.append(f"unregistered topic resource: {resource}")
    if len(resources) != len(set(resources)):
        errors.append("duplicate topic resources")
    return errors


def matrix(root: Path) -> str:
    lines = ["| Capability | Status | Owner | Existing documentation | Next change |",
             "| --- | --- | --- | --- | --- |"]
    for row in load(root, "capabilities.json")["capabilities"]:
        docs = ", ".join(f"`{path}`" for path in row["existing_docs"])
        cells = [row["title"], row["status"], row["owner"], docs, row["gap"]]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", action="store_true", help="print the capability matrix as Markdown")
    args = parser.parse_args()
    try:
        errors = validate(ROOT)
    except (OSError, ValueError, SyntaxError, TypeError) as exc:
        print(f"Inventory validation failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.matrix:
        print(matrix(ROOT))
    else:
        capabilities = load(ROOT, "capabilities.json")["capabilities"]
        groups = load(ROOT, "documents.json")["groups"]
        print(f"Validated {len(capabilities)} capability families, "
              f"{sum(len(group['files']) for group in groups)} documents and "
              f"{len(topic_resources(ROOT))} packaged topics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
