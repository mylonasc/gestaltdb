#!/usr/bin/env python3
"""GestaltDB Documentation & API Drift Checker.

Validates that agent-facing documentation, modular reference guides, and code
snippets in Markdown files accurately reflect the current gestaltdb codebase.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
import re
import sys
from typing import NamedTuple


class Diagnostic(NamedTuple):
    file_path: str
    line_number: int | None
    severity: str  # "ERROR" or "WARNING"
    message: str


class DocChecker:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.diagnostics: list[Diagnostic] = []
        self._ensure_repo_on_sys_path()

    def _ensure_repo_on_sys_path(self):
        src_path = str(self.repo_root / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

    def add_error(self, file_path: str | Path, message: str, line: int | None = None):
        rel_path = self._rel(file_path)
        self.diagnostics.append(Diagnostic(rel_path, line, "ERROR", message))

    def add_warning(self, file_path: str | Path, message: str, line: int | None = None):
        rel_path = self._rel(file_path)
        self.diagnostics.append(Diagnostic(rel_path, line, "WARNING", message))

    def _rel(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.repo_root.resolve()))
        except ValueError:
            return str(path)

    def check_package_exports(self):
        """Verify gestaltdb package root exports against __all__."""
        try:
            import gestaltdb
        except Exception as e:
            self.add_error("src/gestaltdb/__init__.py", f"Failed to import gestaltdb: {e}")
            return

        all_symbols = getattr(gestaltdb, "__all__", [])
        if not all_symbols:
            self.add_error("src/gestaltdb/__init__.py", "gestaltdb.__all__ is empty or missing")
            return

        for symbol in all_symbols:
            if not hasattr(gestaltdb, symbol):
                self.add_error(
                    "src/gestaltdb/__init__.py",
                    f"Symbol '{symbol}' in __all__ is not exposed on gestaltdb module",
                )

        # Confirm that core entities are NOT in __all__ (as intended by gestaltdb architecture)
        disallowed_root_exports = ["GraphDB", "Node", "Edge", "LevelDBStore", "LMDBStore", "PyRexStore"]
        for name in disallowed_root_exports:
            if name in all_symbols:
                self.add_warning(
                    "src/gestaltdb/__init__.py",
                    f"Core object '{name}' found in gestaltdb.__all__. GestaltDB intentionally requires "
                    f"explicit submodule imports for core objects (e.g. from gestaltdb.graphdb import {name}).",
                )

    def check_registries(self):
        """Verify backend and serializer registries in GraphDB match documentation."""
        try:
            from gestaltdb.graphdb import _backend_registry, _serializer_registry
            from gestaltdb.kvstores import KVStore
            from gestaltdb.serializers import Serializer
        except Exception as e:
            self.add_error("src/gestaltdb/graphdb.py", f"Failed to import registry helpers: {e}")
            return

        backend_reg = _backend_registry()
        expected_backends = {"lmdb", "leveldb", "pyrex"}
        actual_backends = set(backend_reg.keys())
        if actual_backends != expected_backends:
            diff = expected_backends.symmetric_difference(actual_backends)
            self.add_error(
                "src/gestaltdb/graphdb.py",
                f"Backend registry mismatch. Expected {expected_backends}, found {actual_backends}. Difference: {diff}",
            )

        for name, cls in backend_reg.items():
            if not inspect.isclass(cls) or not issubclass(cls, KVStore):
                self.add_error(
                    "src/gestaltdb/graphdb.py",
                    f"Registered backend '{name}' ({cls}) does not inherit from KVStore",
                )

        serializer_reg = _serializer_registry()
        expected_serializers = {"pickle", "json", "messagepack", "protobuf"}
        actual_serializers = set(serializer_reg.keys())
        if actual_serializers != expected_serializers:
            diff = expected_serializers.symmetric_difference(actual_serializers)
            self.add_error(
                "src/gestaltdb/graphdb.py",
                f"Serializer registry mismatch. Expected {expected_serializers}, found {actual_serializers}. Difference: {diff}",
            )

        for name, cls in serializer_reg.items():
            if not inspect.isclass(cls) or not issubclass(cls, Serializer):
                self.add_error(
                    "src/gestaltdb/graphdb.py",
                    f"Registered serializer '{name}' ({cls}) does not inherit from Serializer",
                )

    def check_core_methods(self):
        """Verify documented methods on key classes exist with expected signatures."""
        try:
            from gestaltdb.graphdb import Edge, GraphDB, Node
            from gestaltdb.sampling import SamplerEngine, SamplerSnapshot
        except Exception as e:
            self.add_error("src/gestaltdb/", f"Failed to import core classes for method check: {e}")
            return

        expected_graphdb_methods = [
            "create",
            "open",
            "put_node",
            "put_nodes",
            "get_node",
            "delete_node",
            "put_edge",
            "put_edges_bulk",
            "get_edge",
            "delete_edge",
            "create_node_property_index",
            "create_edge_property_index",
            "nodes_by_label",
            "nodes_by_property",
            "nodes_by_label_property",
            "nodes_by_property_range",
            "nodes_by_label_property_range",
            "edges_by_property",
            "edges_by_type_property",
            "edges_by_property_range",
            "edges_by_type_property_range",
            "query",
            "sample_neighbors",
            "sample_typed_paths",
            "sample_typed_subgraph",
            "ingest_arrow",
            "ingest_polars",
            "build_sampler_snapshot",
            "rebuild_deferred_indexes",
            "rebuild_typed_adjacency",
            "close",
        ]
        for meth in expected_graphdb_methods:
            if not hasattr(GraphDB, meth):
                self.add_error("src/gestaltdb/graphdb.py", f"Expected GraphDB method '{meth}' is missing")

        dummy_node = Node(node_id="test", labels=["Test"], properties={"prop": 1})
        for attr in ["get_id", "labels", "properties", "to_dict", "from_dict"]:
            if not hasattr(dummy_node, attr):
                self.add_error("src/gestaltdb/graphdb.py", f"Expected Node attribute/method '{attr}' is missing")

        dummy_edge = Edge(edge_id="e1", source="s", target="t", properties={"type": "rel"})
        for attr in ["get_id", "source", "target", "properties", "to_dict", "from_dict"]:
            if not hasattr(dummy_edge, attr):
                self.add_error("src/gestaltdb/graphdb.py", f"Expected Edge attribute/method '{attr}' is missing")

        for meth in ["load", "sample_neighbors", "sample_multihop", "sample_subgraph"]:
            if not hasattr(SamplerEngine, meth):
                self.add_error("src/gestaltdb/sampling/engine.py", f"Expected SamplerEngine method '{meth}' is missing")

        for meth in ["build", "external_node_id", "external_edge_id", "external_relation_id", "global_triple_to_external"]:
            if not hasattr(SamplerSnapshot, meth):
                self.add_error("src/gestaltdb/sampling/snapshot.py", f"Expected SamplerSnapshot method '{meth}' is missing")

    def check_markdown_files(self, file_paths: list[Path]):
        """Parse all Python code snippets in markdown files and check syntax & imports."""
        try:
            import gestaltdb
        except Exception:
            return

        root_exports = set(getattr(gestaltdb, "__all__", []))

        for file_path in file_paths:
            if not file_path.exists():
                self.add_error(file_path, "Referenced markdown file does not exist")
                continue

            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()

            # Find fenced python code blocks
            in_code = False
            start_line = 0
            code_lines = []

            for idx, line in enumerate(lines, start=1):
                if line.strip().startswith("```python"):
                    in_code = True
                    start_line = idx
                    code_lines = []
                elif line.strip() == "```" and in_code:
                    in_code = False
                    code_text = "\n".join(code_lines)
                    self._check_code_snippet(file_path, start_line, code_text, root_exports)
                elif in_code:
                    code_lines.append(line)

    def _check_code_snippet(
        self,
        file_path: Path,
        start_line: int,
        code_text: str,
        root_exports: set[str],
    ):
        """Validate AST syntax and import statements for a snippet."""
        try:
            tree = ast.parse(code_text)
        except SyntaxError as e:
            self.add_error(
                file_path,
                f"Syntax error in python code block: {e.msg} (at code line {e.lineno})",
                line=start_line + (e.lineno or 0),
            )
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                # Check root gestaltdb imports
                if module_name == "gestaltdb":
                    for alias in node.names:
                        sym = alias.name
                        if sym not in root_exports:
                            self.add_error(
                                file_path,
                                f"Snippet imports '{sym}' from 'gestaltdb' root, but '{sym}' is NOT in "
                                f"gestaltdb.__all__. Use explicit submodule import (e.g. gestaltdb.graphdb).",
                                line=start_line + node.lineno,
                            )
                elif module_name.startswith("gestaltdb."):
                    submod_name = module_name
                    try:
                        submod = importlib.import_module(submod_name)
                        for alias in node.names:
                            sym = alias.name
                            if not hasattr(submod, sym):
                                self.add_error(
                                    file_path,
                                    f"Snippet imports '{sym}' from '{submod_name}', but '{sym}' does not exist there.",
                                    line=start_line + node.lineno,
                                )
                    except ImportError as ie:
                        # Could be an optional dependency backend
                        self.add_warning(
                            file_path,
                            f"Could not import submodule '{submod_name}': {ie}",
                            line=start_line + node.lineno,
                        )

    def check_docs_rst_exist(self):
        """Verify documented Sphinx rst files exist in docs/."""
        expected_docs = [
            "docs/index.rst",
            "docs/quickstart.rst",
            "docs/storage-backends.rst",
            "docs/serializers.rst",
            "docs/typed-sampling.rst",
            "docs/cypher.rst",
            "docs/api.rst",
        ]
        for doc in expected_docs:
            p = self.repo_root / doc
            if not p.exists():
                self.add_error(doc, f"Referenced Sphinx doc file '{doc}' does not exist")

    def run_all(self, extra_markdown_paths: list[Path] | None = None) -> int:
        """Run all verification checks and print diagnostics."""
        print("🔍 Checking GestaltDB documentation consistency...")

        self.check_package_exports()
        self.check_registries()
        self.check_core_methods()
        self.check_docs_rst_exist()

        md_files = [
            self.repo_root / "AGENTS.md",
            self.repo_root / "EXAMPLES.md",
        ]
        ref_dir = self.repo_root / ".opencode/skills/gestaltdb-docs-maintainer/references"
        if ref_dir.exists():
            md_files.extend(ref_dir.glob("*.md"))

        if extra_markdown_paths:
            md_files.extend(extra_markdown_paths)

        self.check_markdown_files(md_files)

        errors = [d for d in self.diagnostics if d.severity == "ERROR"]
        warnings = [d for d in self.diagnostics if d.severity == "WARNING"]

        print(f"\nChecked {len(md_files)} markdown files & core API surface.")

        if warnings:
            print(f"\n⚠️  {len(warnings)} Warning(s):")
            for w in warnings:
                line_str = f":{w.line_number}" if w.line_number else ""
                print(f"  [{w.severity}] {w.file_path}{line_str} -> {w.message}")

        if errors:
            print(f"\n❌ {len(errors)} Error(s) detected:")
            for e in errors:
                line_str = f":{e.line_number}" if e.line_number else ""
                print(f"  [{e.severity}] {e.file_path}{line_str} -> {e.message}")
            return 1
        else:
            print("\n✅ All documentation and API checks PASSED! Zero drift detected.")
            return 0


def main():
    repo_root = Path(__file__).resolve().parents[4]
    checker = DocChecker(repo_root)
    sys.exit(checker.run_all())


if __name__ == "__main__":
    main()
