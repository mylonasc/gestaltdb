"""Offline inventory and Sphinx labels for reviewed Python/Cypher examples.

The catalog classifies source blocks, not generated backend/codec tabs. New or
changed code needs an explicit review; listing blocks never assigns a status.
This module imports no Sphinx, GestaltDB or optional runtime until needed.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import textwrap
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path("docs/_ext/example_inventory.json")
KINDS = {"standalone", "continuation", "illustrative", "pseudocode"}
RUNNERS = {"execute", "variants", "compile", "parse"}
EXTRAS = {
    "lmdb": "lmdb", "leveldb": "plyvel", "rocksdb": "pyrex",
    "msgpack": "msgpack", "protobuf": "google.protobuf", "arrow": "pyarrow",
    "polars": "polars", "networkx": "networkx", "rdf": "rdflib", "torch": "torch",
}
LABELS = {
    "standalone": "Standalone example",
    "continuation": "Staged continuation",
    "illustrative": "Illustrative fragment (not standalone)",
    "pseudocode": "Pseudocode (not executable)",
}


def documents(root: Path) -> list[Path]:
    return sorted([
        *(root / "docs").glob("*.rst"),
        *(root / "src/gestaltdb/agent_skill").rglob("*.md"),
        *(root / name for name in ("README.md", "EXAMPLES.md", "AGENTS.md")),
    ])


def blocks(source: str, path: str) -> list[dict]:
    """Extract code with line/offset identity, including RST options/nesting."""
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result, counts = [], Counter()
    index = 0
    while index < len(lines):
        start = index
        if path.endswith(".rst"):
            match = re.match(r"^( *)\.\. (?:code-block|code):: (python|cypher)\s*$", lines[index])
            if not match:
                index += 1
                continue
            indent, language = len(match[1]), match[2]
            index += 1
            while index < len(lines) and (
                not lines[index].strip()
                or re.match(r"^ {%d,}:[\w-]+:" % (indent + 1), lines[index])
            ):
                index += 1
            body = index
            while index < len(lines) and (
                not lines[index].strip()
                or len(lines[index]) - len(lines[index].lstrip(" ")) > indent
            ):
                index += 1
            code = textwrap.dedent("".join(lines[body:index])).strip("\n")
        else:
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(\w*)\s*$", lines[index])
            if not match:
                index += 1
                continue
            fence, language = match[1], match[2]
            index += 1
            body = index
            while index < len(lines) and not re.match(
                r"^ {0,3}" + re.escape(fence[0]) + r"{%d,}\s*$" % len(fence), lines[index]
            ):
                index += 1
            if index == len(lines):
                raise ValueError(f"{path}:{start + 1}: unclosed code fence")
            code = textwrap.dedent("".join(lines[body:index])).strip("\n")
            index += 1
            if language not in {"python", "cypher"}:
                continue
        ordinal = counts[language]
        counts[language] += 1
        result.append({
            "id": f"{path}::{language}:{ordinal}", "path": path, "language": language,
            "index": ordinal, "line": start + 1, "start": offsets[start], "code": code,
            "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        })
    return result


def discover(root: Path = ROOT) -> list[dict]:
    return [block for path in documents(root)
            for block in blocks(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())]


def page_digest(items: list[dict]) -> str:
    return hashlib.sha256("\n".join(item["sha256"] for item in items).encode()).hexdigest()


def declarations(root: Path = ROOT) -> list[dict]:
    value = json.loads((root / CATALOG).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("examples"), list):
        raise ValueError("expected schema_version 1 example catalog")
    pages = value["examples"]
    known = {path.relative_to(root).as_posix() for path in documents(root)}
    paths = [page.get("path") for page in pages]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate catalog page")
    for page in pages:
        if page.get("path") not in known:
            raise ValueError(f"unknown catalog page: {page.get('path')}")
        if not re.fullmatch(r"[0-9a-f]{64}", page.get("sha256", "")):
            raise ValueError(f"{page['path']}: expected source-code digest")
    return pages


def catalog(root: Path = ROOT) -> list[dict]:
    result = []
    for page in declarations(root):
        items = blocks((root / page["path"]).read_text(encoding="utf-8"), page["path"])
        if page_digest(items) != page["sha256"]:
            raise ValueError(f"{page['path']}: code changed; review execution scope and refresh page digest")
        for runner, indices, extras in page["groups"]:
            kind = "standalone" if runner in {"execute", "variants"} else "illustrative"
            if runner == "pseudocode":
                kind, runner = "pseudocode", "compile"
            if runner == "continuation":
                kind, runner = "continuation", "compile"
            reason = ("Defines its imports and inputs; inspect the result/assertions."
                      if kind == "standalone" else page.get("requires", "Requires application-owned state named in the surrounding section."))
            group = {"kind": kind, "runner": runner, "extras": extras, "reason": reason}
            if kind == "continuation":
                group["setup"] = page.get("setup")
            for index in indices:
                if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
                    raise ValueError(f"{page['path']}: invalid example index {index!r}")
                result.append({**group, **items[index]})
    return result


def validate(root: Path = ROOT) -> list[str]:
    found = {block["id"]: block for block in discover(root)}
    rows = catalog(root)
    errors = []
    ids = [row.get("id") for row in rows]
    for name, count in Counter(ids).items():
        if count > 1:
            errors.append(f"duplicate example: {name}")
    declared = set(ids)
    errors.extend(f"unclassified example: {name}" for name in sorted(found.keys() - declared))
    errors.extend(f"missing example: {name}" for name in sorted(declared - found.keys()))
    for row in rows:
        name = row["id"]
        if name not in found:
            continue
        block = found[name]
        if row.get("kind") not in KINDS or row.get("runner") not in RUNNERS:
            errors.append(f"{name}: invalid kind/runner")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            errors.append(f"{name}: explain execution scope/expected result")
        extras = row.get("extras")
        if not isinstance(extras, list) or any(extra not in EXTRAS for extra in extras):
            errors.append(f"{name}: unknown extras")
        if row.get("kind") == "standalone" and row.get("runner") not in {"execute", "variants"}:
            errors.append(f"{name}: standalone examples must execute")
        if row.get("runner") in {"execute", "variants"} and row.get("kind") != "standalone":
            errors.append(f"{name}: fragments must not execute as standalone")
        if row.get("kind") == "continuation" and not row.get("setup"):
            errors.append(f"{name}: continuation must name its setup")
        if row.get("kind") == "pseudocode":
            continue
        if block["language"] == "python":
            try:
                compile(block["code"], name, "exec")
            except SyntaxError as exc:
                errors.append(f"{name}: invalid Python: {exc}")
        elif row.get("runner") != "parse":
            errors.append(f"{name}: Cypher needs explicit parser coverage")
    return errors


def literal_queries(code: str) -> list[str]:
    """Constant Python .query(...) strings, including implicit concatenation.

    Dynamic strings are intentionally not evaluated. Their surrounding standalone
    workflow is executed where classified; reference fragments remain compile-only.
    """
    return [node.args[0].value for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "query" and node.args
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)]


def status_text(row: dict) -> str:
    extras = row.get("extras", [])
    requirement = "gestaltdb" + ("[" + ",".join(extras) + "]" if extras else "")
    if row["runner"] == "variants":
        suffix = "Choose the capability-qualified installation tab below."
    elif row["kind"] == "standalone":
        suffix = f'Install with ``python -m pip install "{requirement}"``.'
    elif row["kind"] == "continuation":
        suffix = row["setup"]
    else:
        suffix = ""
    return f"**{LABELS[row['kind']]}.** {suffix}".rstrip()


def local_link_errors(root: Path = ROOT) -> list[str]:
    """Check local Markdown destinations; Sphinx checks RST roles and anchors.

    No network requests are made. External URLs and same-page fragments are not
    tested here; this is not an HTTP link checker or a GitHub anchor validator.
    """
    errors = []
    for path in documents(root):
        if path.suffix != ".md":
            continue
        for match in re.finditer(r"!?\[[^\]\n]*\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
            target = urlsplit(match[1].strip("<>"))
            if target.scheme or target.netloc or not target.path:
                continue
            destination = (path.parent / unquote(target.path)).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f"{path.relative_to(root)}: missing local link {match[1]}")
    return errors


def setup(app):
    """Annotate canonical RST blocks before backend-tab source rewriting."""
    from sphinx.errors import SphinxError

    reviewed = None

    def annotate(app, docname, source):
        nonlocal reviewed
        root = Path(app.srcdir).parent
        path = f"docs/{docname}.rst"
        if reviewed is None:
            reviewed = {row["id"]: row for row in catalog(root)}
        app.env.note_dependency(str(root / CATALOG))
        for block in reversed(blocks(source[0], path)):
            row = reviewed.get(block["id"])
            if row is None or row.get("sha256") != block["sha256"]:
                raise SphinxError(f"{block['id']}: unreviewed example; run example_inventory.py")
            # Preserve directive indentation for examples nested inside lists/tabs.
            prefix = re.match(r" *", source[0][block["start"]:])[0]
            text = prefix + status_text(row) + "\n\n"
            source[0] = source[0][:block["start"]] + text + source[0][block["start"]:]

    app.connect("source-read", annotate, priority=400)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list current IDs/digests without classifying them")
    args = parser.parse_args()
    if args.list:
        for path in documents(ROOT):
            relative = path.relative_to(ROOT).as_posix()
            items = blocks(path.read_text(encoding="utf-8"), relative)
            if items:
                print(json.dumps({"path": relative, "sha256": page_digest(items), "count": len(items)}))
        return 0
    try:
        errors = validate() + local_link_errors()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Example inventory failed: {exc}")
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    counts = Counter(row["kind"] for row in catalog())
    print(f"Validated {sum(counts.values())} source examples: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
