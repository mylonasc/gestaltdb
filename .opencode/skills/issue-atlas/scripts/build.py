#!/usr/bin/env python3
"""Portable, read-only GitHub export and self-contained D3 issue atlas."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request

D3_URL = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
D3_SHA256 = "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"
DEFAULTS = {"repo": None, "days": 30, "workers": 6, "title": None,
            "snapshot": "artifacts/issue-atlas/snapshot.json",
            "output": "artifacts/issue-atlas/index.html",
            "d3": "artifacts/issue-atlas/d3.v7.9.0.min.js", "roadmap": None,
            "comments": True}
PATH_KEYS = ("snapshot", "output", "d3", "roadmap")


def validate_roadmap(value):
    """Validate optional generic issue metadata/edge overlay before GitHub calls."""
    if not isinstance(value, dict) or set(value) - {"nodes", "links"}:
        raise ValueError("roadmap must contain only nodes and links")
    for field in ("nodes", "links"):
        if not isinstance(value.get(field, []), list):
            raise ValueError(f"roadmap {field} must be a list")
    allowed = {"number", "kind", "phase", "roadmap_id", "description", "delivery", "acceptance"}
    seen = set()
    for node in value.get("nodes", []):
        if not isinstance(node, dict) or set(node) - allowed:
            raise ValueError("unknown roadmap node field")
        number = node.get("number")
        if type(number) is not int or number < 1 or number in seen:
            raise ValueError("roadmap node numbers must be unique positive integers")
        seen.add(number)
        if node.get("kind", "feature") not in ("roadmap", "epic", "feature", "issue"):
            raise ValueError("unknown roadmap node kind")
        for key in ("phase", "roadmap_id", "description", "delivery"):
            if key in node and not isinstance(node[key], str):
                raise ValueError(f"roadmap {key} must be text")
        if "acceptance" in node and (not isinstance(node["acceptance"], list) or
                                     not all(isinstance(s, str) for s in node["acceptance"])):
            raise ValueError("roadmap acceptance must be a list of strings")
    for edge in value.get("links", []):
        if not isinstance(edge, dict) or set(edge) - {"source", "target", "kind", "origin"}:
            raise ValueError("unknown roadmap link field")
        if any(type(edge.get(key)) is not int or edge[key] < 1 for key in ("source", "target")):
            raise ValueError("roadmap link endpoints must be positive issue numbers")
        if edge.get("kind") not in ("dependency", "parent", "reference"):
            raise ValueError("unknown roadmap link kind")
        if "origin" in edge and not isinstance(edge["origin"], str):
            raise ValueError("roadmap link origin must be text")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON configuration; CLI options override it")
    parser.add_argument("--repo", help="owner/repo; otherwise infer from current git checkout on refresh")
    parser.add_argument("--days", type=int, help="Recently closed window; all open issues included")
    parser.add_argument("--title", help="Dashboard title; defaults to the repository name")
    for key in PATH_KEYS:
        parser.add_argument("--" + key, type=Path)
    parser.add_argument("--refresh", action="store_true", help="Read GitHub with authenticated gh (no writes)")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--no-comments", action="store_true", default=None,
                        help="Skip comment fetches; still include native relationships and issue-body context")
    args = parser.parse_args(argv)
    settings = dict(DEFAULTS)
    if args.config:
        settings_file = args.config.resolve()
        config = json.loads(settings_file.read_text())
        if not isinstance(config, dict) or set(config) - set(DEFAULTS):
            parser.error("unknown configuration fields; see the skill configuration reference")
        settings.update(config)
        for key in PATH_KEYS:
            if key in config and config[key] is not None:
                settings[key] = settings_file.parent / config[key]
    for key in DEFAULTS:
        if key != "comments" and getattr(args, key, None) is not None:
            settings[key] = getattr(args, key)
    if args.no_comments:
        settings["comments"] = False
    if type(settings["days"]) is not int or settings["days"] < 1:
        parser.error("days must be a positive integer")
    if type(settings["workers"]) is not int or not 1 <= settings["workers"] <= 12:
        parser.error("workers must be an integer in 1..12")
    if settings["repo"] is not None and (not isinstance(settings["repo"], str) or
            not re.fullmatch(r"[\w.-]+/[\w.-]+", settings["repo"])):
        parser.error("repo must be owner/repo")
    if type(settings["comments"]) is not bool:
        parser.error("comments must be a boolean")
    if settings["title"] is not None and not isinstance(settings["title"], str):
        parser.error("title must be text")
    for key in PATH_KEYS:
        if settings[key] is not None:
            settings[key] = Path(settings[key]).expanduser().resolve()
    # Never overwrite a snapshot or script cache with another artifact.
    paths = [settings[key] for key in PATH_KEYS if settings[key] is not None]
    if len(paths) != len(set(paths)):
        parser.error("snapshot, output, d3 and roadmap paths must be distinct")
    return argparse.Namespace(**settings, refresh=args.refresh)


def api(endpoint):
    """Paginate list endpoints without changing any repository data."""
    result = subprocess.run(["gh", "api", "--paginate", endpoint],
                            text=True, capture_output=True, check=True)
    # Older gh versions emit concatenated JSON pages, without --slurp.
    decoder, remaining, pages = json.JSONDecoder(), result.stdout.strip(), []
    while remaining:
        page, end = decoder.raw_decode(remaining)
        pages.append(page)
        remaining = remaining[end:].lstrip()
    return [item for page in pages for item in page] if pages and isinstance(pages[0], list) else pages[0]


def fetch_snapshot(repo, days, workers=6, roadmap=None, comments=True):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = f"repos/{repo}"
    # All open issues, regardless of age; recent closed by actual close time.
    issues = api(f"{base}/issues?state=open&per_page=100")
    issues += api(f"{base}/issues?state=closed&since={cutoff.isoformat()}&per_page=100")
    issues = {row["number"]: row for row in issues if "pull_request" not in row and (
        row["state"] == "open" or datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00")) >= cutoff)}
    roadmap = validate_roadmap(roadmap or {})
    required = {node["number"] for node in roadmap.get("nodes", [])}
    required.update(edge[key] for edge in roadmap.get("links", []) for key in ("source", "target"))
    for number in sorted(required - issues.keys()):
        row = api(f"{base}/issues/{number}")
        if "pull_request" in row:
            raise ValueError(f"roadmap #{number} is a pull request, not an issue")
        issues[number] = row

    def enrich(row):
        number = row["number"]
        dependencies = api(f"{base}/issues/{number}/dependencies/blocked_by?per_page=100")
        children = api(f"{base}/issues/{number}/sub_issues?per_page=100")
        discussion = api(f"{base}/issues/{number}/comments?per_page=100") if comments and row["comments"] else []
        return {"issue": row, "dependencies": dependencies, "children": children, "comments": discussion}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        enriched = list(pool.map(enrich, sorted(issues.values(), key=lambda row: row["number"])))
    # Preserve the metadata of referenced native dependencies outside the date
    # window, without recursively expanding unlimited historical context.
    external = {}
    for entry in enriched:
        for row in entry["dependencies"] + entry["children"]:
            if row["number"] not in issues and row.get("repository_url", "").endswith("/" + repo):
                external[row["number"]] = row
    return {"repository": repo, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "days": days, "issues": enriched, "external": list(external.values()),
            "overlay": roadmap, "comments_included": comments, "schema_version": 1}


def brief(body, limit=420):
    summary = re.search(r"^#{1,6}\s+(?:Problem|Summary|Goal)[^\n]*\n+(.+?)(?=^#{1,6}\s|\Z)",
                        body or "", flags=re.M | re.S | re.I)
    if summary:
        body = summary[1]
    body = re.sub(r"```.*?```", " ", body or "", flags=re.S)
    body = re.sub(r"!?\[([^]]+)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"[#*_`>|]", " ", body)
    body = " ".join(body.split())
    return body[:limit] + ("…" if len(body) > limit else "")


def graph_data(snapshot):
    nodes, edges = {}, {}
    repo = snapshot["repository"]

    def add_node(row, external=False):
        number = row["number"]
        nodes[str(number)] = {
            "id": str(number), "number": number, "title": row["title"],
            "description": brief(row.get("body")), "state": row["state"],
            "state_reason": row.get("state_reason"), "url": row["html_url"],
            "labels": [item["name"] for item in row.get("labels", [])],
            "updated_at": row.get("updated_at"), "closed_at": row.get("closed_at"),
            "kind": "issue", "external": external, "phase": "Other issues",
        }

    def link(source, target, kind, origin):
        source, target = str(source), str(target)
        if source != target:
            edges[(source, target, kind)] = {"source": source, "target": target,
                                          "kind": kind, "origin": origin}

    for row in snapshot.get("external", []):
        add_node(row, True)
    for entry in snapshot["issues"]:
        add_node(entry["issue"])
    for entry in snapshot["issues"]:
        number = entry["issue"]["number"]
        for row in entry["dependencies"]:
            link(row["number"], number, "dependency", "GitHub blocked-by")
        for row in entry["children"]:
            link(number, row["number"], "parent", "GitHub sub-issue")
        texts = [entry["issue"].get("body") or ""] + [c["body"] or "" for c in entry["comments"]]
        for text in texts:
            # Ordinary mentions NEVER become dependency claims.
            for match in re.finditer(r"(?<![\w/])#(\d+)\b", text):
                if match[1] in nodes:
                    link(number, match[1], "reference", "Body/comment mention (not a dependency)")

    overlay = validate_roadmap(snapshot.get("overlay", {}))
    required = {str(node["number"]) for node in overlay.get("nodes", [])}
    required.update(str(edge[key]) for edge in overlay.get("links", []) for key in ("source", "target"))
    if required - nodes.keys():
        raise ValueError("roadmap issues missing from snapshot; use --refresh to fetch their metadata")
    for item in overlay.get("nodes", []):
        node = nodes.get(str(item["number"]))
        if node:
            node.update({key: value for key, value in item.items() if key != "number"})
            if "description" in item:
                node["description"] = brief(item["description"])
    for edge in overlay.get("links", []):
        link(edge["source"], edge["target"], edge["kind"], edge.get("origin", "Configured roadmap"))
    return {"nodes": list(nodes.values()), "links": [edge for edge in edges.values()
            if edge["source"] in nodes and edge["target"] in nodes],
            "repository": repo, "fetched_at": snapshot["fetched_at"], "days": snapshot["days"],
            "title": snapshot.get("title") or f"{repo} · Issue atlas"}


def render(data, d3):
    template = Path(__file__).with_name("dashboard.html").read_text()
    # Safe even if GitHub bodies contain </script> or HTML payloads. UI uses
    # textContent, never inserts issue-controlled strings as HTML.
    encoded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    license_text = Path(__file__).with_name("D3-LICENSE.txt").read_text()
    return template.replace("/*__D3__*/", "/*\n" + license_text + "\n*/\n" + d3).replace("/*__DATA__*/", encoded)


def main(argv=None):
    args = parse_args(argv)
    overlay = validate_roadmap(json.loads(args.roadmap.read_text())) if args.roadmap else None
    if args.refresh:
        repo = args.repo or subprocess.check_output(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], text=True).strip()
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("could not infer owner/repo; pass --repo explicitly")
        snapshot = fetch_snapshot(repo, args.days, args.workers, overlay, args.comments)
        snapshot["title"] = args.title
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(snapshot, indent=2) + "\n")
    else:
        snapshot = json.loads(args.snapshot.read_text())
        if args.repo and snapshot["repository"] != args.repo:
            raise ValueError("snapshot repository differs from --repo; refresh or select the matching snapshot")
        if overlay is not None:
            snapshot["overlay"] = overlay
        if args.title is not None:
            snapshot["title"] = args.title
    if not args.d3.exists():
        args.d3.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(D3_URL, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != D3_SHA256:
            raise ValueError("Downloaded D3 does not match the pinned 7.9.0 checksum")
        args.d3.write_bytes(payload)
    if hashlib.sha256(args.d3.read_bytes()).hexdigest() != D3_SHA256:
        raise ValueError("D3 cache must contain the pinned 7.9.0 distribution")
    data = graph_data(snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data, args.d3.read_text()))
    print(json.dumps({"output": str(args.output), "nodes": len(data["nodes"]), "links": len(data["links"]),
                      "d3_sha256": hashlib.sha256(args.d3.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
