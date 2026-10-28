"""Report test execution and skip evidence by category marker.

Markers describe tests; they never gate or skip anything (see
``tests/category_markers.py``). This script replaces the single aggregate
pass count with per-category evidence:

- collection mode (default): collect the suite and count tests per marker.
  Tests with none of the integration markers are reported as ``unit``.
- ``--junitxml`` mode: map a JUnit report's cases back onto the collected
  categories and report passed/failed/skipped/error per category.

Examples::

    python scripts/pytest_category_summary.py
    python -m pytest -q -ra --junitxml=tests.xml
    python scripts/pytest_category_summary.py --junitxml=tests.xml
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CATEGORIES = ("backend", "framework", "native", "fault", "subprocess")


class _Collector:
    def __init__(self):
        self.cases = {}

    def pytest_collection_finish(self, session):
        for item in session.items:
            markers = {mark.name for mark in item.iter_markers()}
            self.cases[item.nodeid] = sorted(markers & set(CATEGORIES))


def collect_categories():
    """Collect the suite and return ``{nodeid: [categories]}``."""
    import contextlib
    import io

    import pytest

    collector = _Collector()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = pytest.main(
            ["--collect-only", "-q", "-p", "no:cacheprovider"], plugins=[collector]
        )
    if rc not in (0, 5):
        sys.stdout.write(buffer.getvalue())
        raise SystemExit(f"collection failed with exit code {rc}")
    return collector.cases


def summarize_collection(cases):
    counts = {category: 0 for category in CATEGORIES}
    counts["unit"] = 0
    for markers in cases.values():
        if markers:
            for marker in markers:
                counts[marker] += 1
        else:
            counts["unit"] += 1
    return counts


def summarize_junit(cases, junit_path):
    """Map JUnit cases onto categories; return per-category outcome counts."""
    tree = ET.parse(junit_path)
    outcomes = {}
    for case in tree.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        nodeid = None
        # classname is "tests.test_module[.TestClass]"; rebuild the node ID.
        if classname == "pytest" or not classname.startswith("tests."):
            nodeid = f"{classname}::{name}" if classname else name
        else:
            rest = classname[len("tests."):]
            module, _, classes = rest.partition(".")
            if classes:
                nodeid = f"tests/{module}.py::{classes}::{name}"
            else:
                nodeid = f"tests/{module}.py::{name}"
        outcomes[nodeid] = "passed"
        for tag in ("failure", "error"):
            if case.find(tag) is not None:
                outcomes[nodeid] = "failed"
        if case.find("skipped") is not None:
            outcomes[nodeid] = "skipped"
    summary = {c: {"passed": 0, "failed": 0, "skipped": 0} for c in (*CATEGORIES, "unit")}
    unknown = 0
    for nodeid, outcome in outcomes.items():
        markers = cases.get(nodeid)
        if markers is None:
            unknown += 1
            continue
        buckets = markers or ["unit"]
        for bucket in buckets:
            summary[bucket][outcome if outcome in summary[bucket] else "failed"] += 1
    return summary, unknown, len(outcomes)


def render_collection(counts, total):
    lines = ["category  tests", "--------  -----"]
    for category in (*CATEGORIES, "unit"):
        lines.append(f"{category:<8}  {counts[category]:>5}")
    lines.append(f"{'total':<8}  {total:>5}")
    return "\n".join(lines)


def render_junit(summary):
    lines = ["category  passed  failed  skipped", "--------  ------  ------  -------"]
    for category in (*CATEGORIES, "unit"):
        row = summary[category]
        lines.append(
            f"{category:<8}  {row['passed']:>6}  {row['failed']:>6}  {row['skipped']:>7}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junitxml", help="JUnit XML from a pytest run")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of tables")
    args = parser.parse_args(argv)
    cases = collect_categories()
    if args.junitxml is None:
        counts = summarize_collection(cases)
        if args.json:
            print(json.dumps({"total": len(cases), "categories": counts}, indent=2))
        else:
            print(render_collection(counts, len(cases)))
        return 0
    summary, unknown, total = summarize_junit(cases, args.junitxml)
    if args.json:
        print(json.dumps(
            {"total": total, "unknown": unknown, "categories": summary}, indent=2))
    else:
        print(render_junit(summary))
        if unknown:
            print(f"unmapped junit cases: {unknown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
