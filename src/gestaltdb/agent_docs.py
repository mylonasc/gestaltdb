"""Queryable agent-facing documentation bundled with GestaltDB.

Run ``python -m gestaltdb.agent_docs --help`` to list, retrieve, search, or
extract examples from the packaged GestaltDB user skill.
"""

from __future__ import annotations

import argparse
from importlib import resources
import re
import sys
from typing import NamedTuple, Sequence


class Topic(NamedTuple):
    topic_id: str
    title: str
    when_to_read: str
    resource: str


TOPICS: dict[str, Topic] = {
    "quickstart": Topic(
        "quickstart",
        "Quickstart For Library Users",
        "Creating graphs, adding nodes/edges, imports, relationship types, and close discipline.",
        "references/quickstart.md",
    ),
    "cypher": Topic(
        "cypher",
        "Cypher For Library Users",
        "Writing read-only GraphDB.query calls and avoiding unsupported Cypher syntax.",
        "references/cypher.md",
    ),
    "indexing": Topic(
        "indexing",
        "Indexing For Library Users",
        "Property indexes, exact/range lookups, and deferred index rebuilds.",
        "references/indexing.md",
    ),
    "sampling": Topic(
        "sampling",
        "Sampling For Library Users",
        "Typed traversal sampling with external IDs and ML snapshot sampling with compact IDs.",
        "references/sampling.md",
    ),
    "ingestion": Topic(
        "ingestion",
        "Ingestion For Library Users",
        "Arrow/Polars ingestion and IndexMaintenanceMode choices.",
        "references/ingestion.md",
    ),
}


def read_skill() -> str:
    return (resources.files("gestaltdb.agent_skill") / "SKILL.md").read_text(encoding="utf-8")


def read_topic(topic_id: str) -> str:
    topic = TOPICS[topic_id]
    return (resources.files("gestaltdb.agent_skill") / topic.resource).read_text(encoding="utf-8")


def extract_python_examples(markdown: str) -> list[str]:
    examples: list[str] = []
    in_code = False
    current: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```python"):
            in_code = True
            current = []
        elif stripped == "```" and in_code:
            in_code = False
            examples.append("\n".join(current))
        elif in_code:
            current.append(line)
    return examples


def strip_code(markdown: str) -> str:
    lines: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(line)
    return "\n".join(lines).strip()


def cmd_list(_args: argparse.Namespace) -> int:
    print("GestaltDB packaged agent docs")
    for topic in TOPICS.values():
        print(f"- {topic.topic_id}: {topic.title}")
        print(f"  when: {topic.when_to_read}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    if args.topic == "skill":
        content = read_skill()
    else:
        if args.topic not in TOPICS:
            print(f"unknown topic: {args.topic}", file=sys.stderr)
            return 2
        content = read_topic(args.topic)

    if args.examples:
        examples = extract_python_examples(content)
        for index, example in enumerate(examples, 1):
            print(f"# --- example {index} ---")
            print(example)
            print()
    elif args.rules:
        print(strip_code(content))
    else:
        print(content)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    pattern = re.compile(args.query, re.IGNORECASE)
    documents = [("skill", read_skill())] + [(topic_id, read_topic(topic_id)) for topic_id in TOPICS]
    matches = 0
    for name, content in documents:
        file_matches = []
        for line_number, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                file_matches.append((line_number, line.strip()))
        if file_matches:
            print(f"[{name}] {len(file_matches)} match(es)")
            for line_number, line in file_matches[:8]:
                print(f"  {line_number}: {line}")
            if len(file_matches) > 8:
                print(f"  ... {len(file_matches) - 8} more")
            matches += len(file_matches)
    print(f"total matches: {matches}")
    return 0 if matches else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query packaged GestaltDB agent documentation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list documentation topics")
    list_parser.set_defaults(func=cmd_list)

    get_parser = subparsers.add_parser("get", help="get one topic or the packaged skill")
    get_parser.add_argument("topic", help="topic id or 'skill'")
    get_parser.add_argument("--examples", action="store_true", help="print only Python examples")
    get_parser.add_argument("--rules", action="store_true", help="print markdown without code blocks")
    get_parser.set_defaults(func=cmd_get)

    search_parser = subparsers.add_parser("search", help="search packaged docs")
    search_parser.add_argument("query", help="regular expression to search for")
    search_parser.set_defaults(func=cmd_search)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
