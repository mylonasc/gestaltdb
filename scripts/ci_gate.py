"""Aggregate applicable pull-request workflows without privileged workflow_run jobs.

The only external dependency is PyYAML, installed by required-ci.yml. Never
execute workflow commands or interpolate PR-controlled strings into a shell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def glob_matches(pattern: str, value: str) -> bool:
    """Match the deliberately bounded GitHub glob subset used by our workflows."""
    if any(char in pattern for char in "[]+?(){}\\"):
        raise ValueError(f"Unsupported workflow glob: {pattern!r}")
    parts = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            parts.append("(?:.*/)?")
            index += 3
        elif pattern[index:index + 2] == "**":
            parts.append(".*")
            index += 2
        else:
            char = pattern[index]
            parts.append("[^/]*" if char == "*" else re.escape(char))
            index += 1
    return re.fullmatch("".join(parts), value) is not None


def selected(patterns: list[str], value: str) -> bool:
    matched = False
    for pattern in patterns:
        negative = pattern.startswith("!")
        if glob_matches(pattern[1:] if negative else pattern, value):
            matched = not negative
    return matched


def applicable(trigger: dict, branch: str, paths: list[str]) -> bool:
    if "branches" in trigger and not selected(trigger["branches"], branch):
        return False
    if "branches-ignore" in trigger and selected(trigger["branches-ignore"], branch):
        return False
    if "paths" in trigger and not any(selected(trigger["paths"], path) for path in paths):
        return False
    if "paths-ignore" in trigger and all(selected(trigger["paths-ignore"], path) for path in paths):
        return False
    # Other event/activity filters require explicit support rather than silently
    # treating a workflow that never starts as optional.
    if set(trigger) - {"branches", "branches-ignore", "paths", "paths-ignore"}:
        raise ValueError(f"Unsupported pull_request filters: {trigger}")
    return True


def required_workflows(root: Path, branch: str, paths: list[str]) -> set[str]:
    import yaml

    required = set()
    for path in sorted((root / ".github/workflows").glob("*.y*ml")):
        if path.name == "required-ci.yml":
            continue  # Never wait for ourselves.
        # BaseLoader keeps the YAML key `on` as text, unlike YAML 1.1 SafeLoader.
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        events = workflow.get("on", {})
        if not isinstance(events, dict):
            raise ValueError(f"Use mapping-style workflow triggers: {path}")
        if "pull_request" in events and applicable(events["pull_request"] or {}, branch, paths):
            required.add(path.relative_to(root).as_posix())
    return required


def changed_paths(event: dict) -> list[str]:
    pr = event["pull_request"]
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ValueError("Invalid commit identity")
    # Three-dot comparison mirrors PR path filtering, without the API's 3,000
    # file truncation. Deleted paths and both sides of renames are included.
    raw = subprocess.check_output(["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"])
    return [path.decode("utf-8") for path in raw.split(b"\0") if path]


def run_states(required: set[str], runs: list[dict], event: dict) -> dict[str, str]:
    """Use only the newest run for this PR/head, never a previous green attempt."""
    pr = event["pull_request"]
    latest = {}
    for run in runs:
        path = run["path"].split("@", 1)[0]
        if (path not in required or run["event"] != "pull_request"
                or run["head_sha"] != pr["head"]["sha"]
                or not any(item["number"] == event["number"] for item in run["pull_requests"])):
            continue
        key = (run["id"], run["run_attempt"])
        if path not in latest or key > (latest[path]["id"], latest[path]["run_attempt"]):
            latest[path] = run
    return {
        path: (latest[path]["conclusion"] or "unknown") if path in latest and latest[path]["status"] == "completed"
        else "pending"
        for path in required
    }


def wait_for_workflows(required: set[str], event: dict, fetch_runs, *, timeout=2100, interval=20,
                       clock=time.monotonic, sleep=time.sleep) -> None:
    deadline = clock() + timeout
    while True:
        states = run_states(required, fetch_runs(), event)
        print(json.dumps(states, sort_keys=True), flush=True)
        failures = {path: state for path, state in states.items() if state not in {"pending", "success"}}
        if failures:
            raise RuntimeError(f"Required CI failed: {failures}")
        if all(state == "success" for state in states.values()):
            return
        if clock() >= deadline:
            raise TimeoutError(f"Required CI did not finish/start: {states}")
        sleep(interval)


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    required = required_workflows(Path.cwd(), event["pull_request"]["base"]["ref"], changed_paths(event))
    print("Required workflows:", sorted(required), flush=True)

    def fetch_runs():
        runs = []
        for page in range(1, 11):
            query = urlencode({"event": "pull_request", "head_sha": event["pull_request"]["head"]["sha"],
                               "per_page": 100, "page": page})
            request = Request(f"https://api.github.com/repos/{repository}/actions/runs?{query}", headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
            with urlopen(request, timeout=30) as response:
                batch = json.load(response)["workflow_runs"]
            runs.extend(batch)
            if len(batch) < 100:
                return runs
        raise RuntimeError("Workflow run pagination exceeded safe limit")

    wait_for_workflows(required, event, fetch_runs)


if __name__ == "__main__":
    main()
