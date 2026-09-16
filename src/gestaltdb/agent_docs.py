"""Queryable agent-facing documentation bundled with GestaltDB.

Run ``python -m gestaltdb.agent_docs --help`` to list, retrieve, search, or
extract examples from the packaged GestaltDB user skill.
"""

from __future__ import annotations

import argparse
from importlib import resources
from pathlib import Path
import re
import shutil
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
        "Creating graphs, adding nodes/edges, imports, persistence, inspection, relationship types, and close discipline.",
        "references/quickstart.md",
    ),
    "backends": Topic(
        "backends",
        "Backends, Persistence, And Inspection",
        "GraphDB.create/open, manifest-backed DBs, LevelDB, PyRex/RocksDB, and inspecting index metadata/properties.",
        "references/backends.md",
    ),
    "cypher": Topic(
        "cypher",
        "Cypher For Library Users",
        "Writing GraphDB.query reads and writes, schema commands, registered procedures, and explicit limitations.",
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


def install_opencode_skill(target_dir: str | Path = ".", *, skill_name: str = "gestaltdb-user-guide", force: bool = False) -> Path:
    """Install the packaged GestaltDB user skill into a project-local opencode skill directory."""
    target = Path(target_dir).expanduser().resolve() / ".opencode" / "skills" / skill_name
    if target.exists():
        if not force:
            raise FileExistsError(f"opencode skill already exists: {target}; pass --force to overwrite packaged files")
        if not target.is_dir():
            raise NotADirectoryError(target)
    target.mkdir(parents=True, exist_ok=True)

    source_root = resources.files("gestaltdb.agent_skill")
    for resource in source_root.iterdir():
        destination = target / resource.name
        if resource.is_dir():
            shutil.copytree(resource, destination, dirs_exist_ok=True)
        else:
            with resources.as_file(resource) as source_path:
                shutil.copy2(source_path, destination)
    return target


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
    hits: list[tuple[int, str, str, int, list[str]]] = []
    for name, content in documents:
        lines = content.splitlines()
        topic = TOPICS.get(name)
        header = f"{topic.title} | when: {topic.when_to_read}" if topic else "Packaged skill overview and routing rules."
        for line_number, line in enumerate(lines, 1):
            if pattern.search(line):
                start = max(1, line_number - args.context)
                end = min(len(lines), line_number + args.context)
                snippet = [f"{number}: {lines[number - 1]}" for number in range(start, end + 1)]
                score = 0 if pattern.search(name) else 1
                hits.append((score, name, header, line_number, snippet))
    hits.sort(key=lambda hit: (hit[0], hit[1], hit[3]))
    total = len(hits)
    if total == 0:
        print("total matches: 0")
        return 1
    limit = max(1, args.limit)
    page = max(1, args.page)
    start_index = (page - 1) * limit
    end_index = start_index + limit
    shown = hits[start_index:end_index]
    page_count = (total + limit - 1) // limit
    print(f"total matches: {total}; page {page}/{page_count}; showing {len(shown)}; limit {limit}; context {args.context}")
    if page < page_count:
        print(f"next page: python -m gestaltdb.agent_docs search {args.query!r} --page {page + 1} --limit {limit} --context {args.context}")
    for _score, name, header, line_number, snippet in shown:
        print()
        print(f"[{name}] line {line_number}")
        print(f"why: {header}")
        print("snippet:")
        for snippet_line in snippet:
            print(f"  {snippet_line}")
    return 0


def cmd_install_opencode_skill(args: argparse.Namespace) -> int:
    try:
        destination = install_opencode_skill(args.target, skill_name=args.name, force=args.force)
    except (FileExistsError, NotADirectoryError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"installed GestaltDB opencode skill: {destination}")
    return 0


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
    search_parser.add_argument("--limit", type=int, default=10, help="maximum matches to print per page; defaults to 10")
    search_parser.add_argument("--page", type=int, default=1, help="1-based results page to print; defaults to 1")
    search_parser.add_argument("--context", type=int, default=2, help="context lines before and after each match; defaults to 2")
    search_parser.set_defaults(func=cmd_search)

    install_parser = subparsers.add_parser(
        "install-opencode-skill",
        help="copy the packaged GestaltDB user skill into .opencode/skills",
    )
    install_parser.add_argument("--target", default=".", help="project directory to install into; defaults to cwd")
    install_parser.add_argument("--name", default="gestaltdb-user-guide", help="opencode skill directory name")
    install_parser.add_argument("--force", action="store_true", help="overwrite packaged skill files if the skill already exists")
    install_parser.set_defaults(func=cmd_install_opencode_skill)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
