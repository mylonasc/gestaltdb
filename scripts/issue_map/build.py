#!/usr/bin/env python3
"""GestaltDB wrapper; the portable implementation lives in the issue-atlas skill."""

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def roadmap_overlay():
    """Project-specific translation into the portable skill's generic schema."""
    roadmap = json.loads((ROOT / "planning/reasoning-engine/roadmap.json").read_text())
    register = json.loads((ROOT / "planning/reasoning-engine/issues.json").read_text())
    ids = {**register["epics"], **register["features"]}
    parent = register["roadmap"]
    nodes = [{"number": parent, "kind": "roadmap", "phase": "Roadmap", "roadmap_id": "RE"}]
    links = []
    for item in roadmap["epics"] + roadmap["features"]:
        is_epic = "epic" not in item
        number = ids[item["id"]]
        nodes.append({"number": number, "kind": "epic" if is_epic else "feature",
                      "phase": item["id"] if is_epic else item["epic"], "roadmap_id": item["id"],
                      "description": item.get("goal") or " ".join(item.get("acceptance", [])),
                      "delivery": roadmap.get("delivery", {}).get(item["id"], {}).get("status", "not recorded"),
                      "acceptance": item.get("acceptance", [])})
        links.append({"source": parent if is_epic else ids[item["epic"]], "target": number,
                      "kind": "parent", "origin": "Local roadmap register"})
        links.extend({"source": ids[key], "target": number, "kind": "dependency",
                      "origin": "Local roadmap.json"} for key in item.get("depends", []))
    return {"nodes": nodes, "links": links}


def main():
    artifact = ROOT / "artifacts/issue-map"
    artifact.mkdir(parents=True, exist_ok=True)
    overlay = artifact / "roadmap-overlay.json"
    overlay.write_text(json.dumps(roadmap_overlay(), indent=2) + "\n")
    path = ROOT / ".opencode/skills/issue-atlas/scripts/build.py"
    spec = importlib.util.spec_from_file_location("portable_issue_atlas", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Later CLI arguments override these project conveniences.
    module.main(["--repo", "mylonasc/gestaltdb", "--roadmap", str(overlay),
                 "--snapshot", str(artifact / "snapshot.json"),
                 "--output", str(artifact / "index.html"),
                 "--d3", str(artifact / "d3.v7.9.0.min.js"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
