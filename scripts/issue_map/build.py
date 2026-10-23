#!/usr/bin/env python3
"""Read-only GitHub export and self-contained D3 issue/roadmap dashboard."""

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

ROOT = Path(__file__).resolve().parents[2]
D3_URL = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
D3_SHA256 = "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"


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


def fetch_snapshot(repo, days, workers=6):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = f"repos/{repo}"
    # All open issues, regardless of age; recent closed by actual close time.
    issues = api(f"{base}/issues?state=open&per_page=100")
    issues += api(f"{base}/issues?state=closed&since={cutoff.isoformat()}&per_page=100")
    issues = {row["number"]: row for row in issues if "pull_request" not in row and (
        row["state"] == "open" or datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00")) >= cutoff)}
    register = json.loads((ROOT / "planning/reasoning-engine/issues.json").read_text())
    required = {register["roadmap"], *register["epics"].values(), *register["features"].values()}
    for number in sorted(required - issues.keys()):
        issues[number] = api(f"{base}/issues/{number}")

    def enrich(row):
        number = row["number"]
        dependencies = api(f"{base}/issues/{number}/dependencies/blocked_by?per_page=100")
        children = api(f"{base}/issues/{number}/sub_issues?per_page=100")
        comments = api(f"{base}/issues/{number}/comments?per_page=100") if row["comments"] else []
        return {"issue": row, "dependencies": dependencies, "children": children, "comments": comments}

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
            "roadmap": json.loads((ROOT / "planning/reasoning-engine/roadmap.json").read_text()),
            "register": register}


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

    roadmap, register = snapshot["roadmap"], snapshot["register"]
    ids = {**register["epics"], **register["features"]}
    parent = register["roadmap"]
    if str(parent) in nodes:
        nodes[str(parent)].update(kind="roadmap", phase="Roadmap", roadmap_id="RE")
    for item in roadmap["epics"] + roadmap["features"]:
        number = ids[item["id"]]
        node = nodes.get(str(number))
        if not node:
            continue
        is_epic = "epic" not in item
        phase = item["id"] if is_epic else item["epic"]
        delivery = roadmap.get("delivery", {}).get(item["id"], {})
        node.update(kind="epic" if is_epic else "feature", phase=phase, roadmap_id=item["id"],
                    delivery=delivery.get("status", "not recorded"),
                    description=brief(item.get("goal") or " ".join(item.get("acceptance", []))),
                    acceptance=item.get("acceptance", []))
        link(parent if is_epic else ids[item["epic"]], number, "parent", "Local roadmap register")
        for dependency in item.get("depends", []):
            link(ids[dependency], number, "dependency", "Local roadmap.json")
    return {"nodes": list(nodes.values()), "links": [edge for edge in edges.values()
            if edge["source"] in nodes and edge["target"] in nodes],
            "repository": repo, "fetched_at": snapshot["fetched_at"], "days": snapshot["days"]}


def render(data, d3):
    template = Path(__file__).with_name("dashboard.html").read_text()
    # Safe even if GitHub bodies contain </script> or HTML payloads. UI uses
    # textContent, never inserts issue-controlled strings as HTML.
    encoded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    license_text = Path(__file__).with_name("D3-LICENSE.txt").read_text()
    return template.replace("/*__D3__*/", "/*\n" + license_text + "\n*/\n" + d3).replace("/*__DATA__*/", encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="mylonasc/gestaltdb")
    parser.add_argument("--days", type=int, default=30, help="Recently closed window; all open issues included")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "artifacts/issue-map/snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/issue-map/index.html")
    parser.add_argument("--refresh", action="store_true", help="Read GitHub with authenticated gh (no writes)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--d3", type=Path, default=ROOT / "artifacts/issue-map/d3.v7.9.0.min.js")
    args = parser.parse_args()
    if args.days < 1 or not 1 <= args.workers <= 12 or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        parser.error("positive days, 1..12 workers and owner/repo required")
    if args.refresh:
        snapshot = fetch_snapshot(args.repo, args.days, args.workers)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(snapshot, indent=2) + "\n")
    else:
        snapshot = json.loads(args.snapshot.read_text())
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
