#!/usr/bin/env python3
"""GestaltDB Documentation & Knowledge Retrieval CLI.

Provides targeted modular documentation retrieval, snippet extraction,
automated drift checking, and example execution for agents and developers.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from typing import NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parents[2]
REFERENCES_DIR = SKILL_DIR / "references"


class TopicMetadata(NamedTuple):
    topic_id: str
    title: str
    when_to_read: str
    related_sources: list[str]
    file_path: Path


TOPIC_REGISTRY: dict[str, TopicMetadata] = {
    "backends": TopicMetadata(
        topic_id="backends",
        title="Storage Backends & Store Lifecycle",
        when_to_read="Working with LevelDB, LMDB, PyRex (RocksDB), transactional mode, or GraphDB.create/open manifests.",
        related_sources=["src/gestaltdb/kvstores.py", "src/gestaltdb/graphdb.py"],
        file_path=REFERENCES_DIR / "backends.md",
    ),
    "indexing": TopicMetadata(
        topic_id="indexing",
        title="Secondary Indexing & Range Queries",
        when_to_read="Working with node labels, relationship types, property indexes, exact lookups, range queries, or deferred indexing.",
        related_sources=["src/gestaltdb/graphdb.py"],
        file_path=REFERENCES_DIR / "indexing.md",
    ),
    "ingestion": TopicMetadata(
        topic_id="ingestion",
        title="Columnar Ingestion (Arrow & Polars)",
        when_to_read="Bulk data ingestion, ingest_arrow, ingest_polars, ColumnarIngestionMode, IndexMaintenanceMode.",
        related_sources=["src/gestaltdb/ingestion.py", "src/gestaltdb/graphdb.py"],
        file_path=REFERENCES_DIR / "ingestion.md",
    ),
    "cypher": TopicMetadata(
        topic_id="cypher",
        title="Cypher Query Language Support & Limitations",
        when_to_read="Writing or modifying Cypher queries, graph.query(), cypher parser/plan/runtime, supported and unsupported syntax.",
        related_sources=["src/gestaltdb/cypher.py", "src/gestaltdb/cypher_*.py"],
        file_path=REFERENCES_DIR / "cypher.md",
    ),
    "sampling": TopicMetadata(
        topic_id="sampling",
        title="Graph Traversal & Array-Native ML Sampling",
        when_to_read="Working with traversal sampling (sample_neighbors, sample_typed_subgraph) or ML array snapshots (SamplerSnapshot, SamplerEngine).",
        related_sources=["src/gestaltdb/sampling/"],
        file_path=REFERENCES_DIR / "sampling.md",
    ),
}


def extract_snippets(content: str) -> list[str]:
    """Extract all fenced python code snippets from markdown."""
    snippets = []
    lines = content.splitlines()
    in_code = False
    current: list[str] = []
    for line in lines:
        if line.strip().startswith("```python"):
            in_code = True
            current = []
        elif line.strip() == "```" and in_code:
            in_code = False
            snippets.append("\n".join(current))
        elif in_code:
            current.append(line)
    return snippets


def extract_rules_only(content: str) -> str:
    """Extract markdown text excluding fenced code blocks."""
    out_lines = []
    in_code = False
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            out_lines.append(line)
    return "\n".join(out_lines).strip()


def cmd_list(args):
    """List all modular topics with relevance triggers and related source files."""
    print("📚 GestaltDB Modular Documentation Topics:")
    print("=" * 72)
    for topic_id, meta in sorted(TOPIC_REGISTRY.items()):
        snippet_count = 0
        if meta.file_path.exists():
            snippet_count = len(extract_snippets(meta.file_path.read_text(encoding="utf-8")))
        print(f"\n🔹 [{topic_id}] {meta.title}")
        print(f"   When to read : {meta.when_to_read}")
        print(f"   Related code : {', '.join(meta.related_sources)}")
        print(f"   Reference doc: {meta.file_path.relative_to(REPO_ROOT)} ({snippet_count} runnable snippet(s))")
    print("\n💡 Agent Tip: Retrieve only the section you need using: python doc_tool.py get <topic>")


def cmd_get(args):
    """Retrieve modular documentation or code examples for a specific topic."""
    topic = args.topic.lower()
    if topic not in TOPIC_REGISTRY:
        print(f"❌ Unknown topic '{topic}'. Available topics: {', '.join(TOPIC_REGISTRY.keys())}", file=sys.stderr)
        sys.exit(1)

    meta = TOPIC_REGISTRY[topic]
    if not meta.file_path.exists():
        print(f"❌ File not found: {meta.file_path}", file=sys.stderr)
        sys.exit(1)

    if args.path:
        print(str(meta.file_path))
        return

    content = meta.file_path.read_text(encoding="utf-8")

    if args.examples:
        snippets = extract_snippets(content)
        if not snippets:
            print(f"# No snippets found in {topic}")
            return
        print(f"# Runnable Examples for [{topic}] ({len(snippets)} snippet(s))\n")
        for idx, snip in enumerate(snippets, start=1):
            print(f"# --- Snippet {idx} ---")
            print(snip)
            print()
    elif args.rules:
        print(extract_rules_only(content))
    else:
        print(content)


def cmd_search(args):
    """Search for a keyword or regex across reference docs and root agent docs."""
    query = args.query
    pattern = re.compile(query, re.IGNORECASE)
    search_files = [
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "EXAMPLES.md",
        *REFERENCES_DIR.glob("*.md"),
    ]

    matches_found = 0
    print(f"🔎 Searching for '{query}' in agent documentation...\n")
    for file_path in search_files:
        if not file_path.exists():
            continue
        rel_path = file_path.relative_to(REPO_ROOT)
        lines = file_path.read_text(encoding="utf-8").splitlines()
        file_matches = []
        for line_no, line in enumerate(lines, start=1):
            if pattern.search(line):
                file_matches.append((line_no, line.strip()))

        if file_matches:
            print(f"📄 {rel_path} ({len(file_matches)} matches):")
            for line_no, snippet in file_matches[:5]:
                print(f"   Line {line_no:4d}: {snippet}")
            if len(file_matches) > 5:
                print(f"   ... and {len(file_matches) - 5} more")
            print()
            matches_found += len(file_matches)

    print(f"Total occurrences found: {matches_found}")


def cmd_check(args):
    """Run automated drift & consistency checks."""
    from check_docs import DocChecker
    checker = DocChecker(REPO_ROOT)
    sys.exit(checker.run_all())


def cmd_test_examples(args):
    """Safely execute code snippets in temporary environments to ensure they run."""
    print("🚀 Executing documentation code snippets...")
    target_topics = [args.topic] if args.topic else list(TOPIC_REGISTRY.keys())

    total_run = 0
    passed = 0
    skipped = 0
    failed = 0

    for topic in target_topics:
        if topic not in TOPIC_REGISTRY:
            print(f"Warning: Skipping unknown topic '{topic}'")
            continue

        meta = TOPIC_REGISTRY[topic]
        if not meta.file_path.exists():
            continue

        snippets = extract_snippets(meta.file_path.read_text(encoding="utf-8"))
        for idx, snip in enumerate(snippets, start=1):
            total_run += 1
            print(f"  Testing [{topic}] snippet {idx}...", end=" ", flush=True)

            # Check for optional dependency preconditions
            if "LMDBStore" in snip:
                try:
                    import lmdb
                except ImportError:
                    print("SKIPPED (lmdb not installed)")
                    skipped += 1
                    continue

            # Execute snippet in a separate python process
            proc = subprocess.run(
                [sys.executable, "-c", snip],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode == 0:
                print("PASSED")
                passed += 1
            else:
                print("FAILED")
                print(f"    Error: {proc.stderr.strip()[:200]}")
                failed += 1

    print(f"\nResults: {passed} passed, {skipped} skipped, {failed} failed out of {total_run} snippets.")
    sys.exit(1 if failed > 0 else 0)


def main():
    parser = argparse.ArgumentParser(
        description="GestaltDB Documentation & Knowledge Retrieval Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all modular documentation topics and when to read them")
    p_list.set_defaults(func=cmd_list)

    # get
    p_get = subparsers.add_parser("get", help="Retrieve documentation or snippets for a topic")
    p_get.add_argument("topic", help="Topic ID (e.g. backends, indexing, ingestion, cypher, sampling)")
    p_get.add_argument("--examples", action="store_true", help="Extract and show only runnable code examples")
    p_get.add_argument("--rules", action="store_true", help="Show only conceptual rules without code examples")
    p_get.add_argument("--path", action="store_true", help="Print absolute path to the reference file")
    p_get.set_defaults(func=cmd_get)

    # search
    p_search = subparsers.add_parser("search", help="Search keywords across agent docs")
    p_search.add_argument("query", help="Keyword or regex pattern to search for")
    p_search.set_defaults(func=cmd_search)

    # check
    p_check = subparsers.add_parser("check", help="Run automated documentation and API drift checks")
    p_check.set_defaults(func=cmd_check)

    # test-examples
    p_test = subparsers.add_parser("test-examples", help="Execute runnable code examples in isolated tests")
    p_test.add_argument("--topic", help="Optional topic to limit execution to")
    p_test.set_defaults(func=cmd_test_examples)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
